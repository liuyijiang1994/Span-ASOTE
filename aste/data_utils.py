import copy
import json
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Set

import numpy as np
import pandas as pd
from pydantic import BaseModel
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from evaluation import TagReader, LinearInstance, nereval
from parsing import PosTagger
from utils import count_joins, get_simple_stats

RawTriple = Tuple[List[int], List[int], int]
RawEntity = Tuple[List[int], str]
Span = Tuple[int, int]


class SplitEnum(str, Enum):
    train = "train"
    dev = "dev"
    test = "test"


class LabelEnum(str, Enum):
    positive = "POS"
    negative = "NEG"
    neutral = "NEU"
    opinion = "OPINION"
    target = "TARGET"

    @classmethod
    def as_list(cls):
        return [cls.neutral, cls.positive, cls.negative]

    @classmethod
    def i_to_label(cls, i: int):
        return cls.as_list()[i]

    @classmethod
    def label_to_i(cls, label) -> int:
        return cls.as_list().index(label)


class Entity(BaseModel):
    start: int
    end: int
    ent_type: str

    @property
    def entity(self) -> Tuple[int, int]:
        return self.start, self.end

    @classmethod
    def from_raw_entity(cls, x: RawEntity):
        (start, end), ent_type = x

        return cls(
            start=start,
            end=end,
            ent_type=ent_type,
        )

    def to_raw_entity(self) -> RawEntity:
        return [self.start, self.end], self.ent_type

    def as_text(self, tokens: List[str]) -> str:
        ent = " ".join(tokens[self.start: self.end + 1])
        return f"{ent} ({self.ent_type})"


class SentimentTriple(BaseModel):
    o_start: int
    o_end: int
    t_start: int
    t_end: int
    label: LabelEnum

    @property
    def opinion(self) -> Tuple[int, int]:
        return self.o_start, self.o_end

    @property
    def target(self) -> Tuple[int, int]:
        return self.t_start, self.t_end

    @classmethod
    def from_raw_triple(cls, x: RawTriple):
        (o_start, o_end), (t_start, t_end), polarity = x

        return cls(
            o_start=o_start,
            o_end=o_end,
            t_start=t_start,
            t_end=t_end,
            label=LabelEnum.i_to_label(polarity),
        )

    def to_raw_triple(self) -> RawTriple:
        polarity = LabelEnum.label_to_i(self.label)

        return [self.o_start, self.o_end], [self.t_start, self.t_end], polarity

    def as_text(self, tokens: List[str]) -> str:
        opinion = " ".join(tokens[self.o_start: self.o_end + 1])
        target = " ".join(tokens[self.t_start: self.t_end + 1])
        return f"{opinion}-{target} ({self.label})"


class TripleHeuristic(BaseModel):
    @staticmethod
    def run(
            opinion_to_label: Dict[Span, LabelEnum], target_to_label: Dict[Span, LabelEnum],
    ) -> List[SentimentTriple]:
        # For each target, pair with the closest opinion (and vice versa)
        spans_o = list(opinion_to_label.keys())
        spans_t = list(target_to_label.keys())
        pos_o = np.expand_dims(np.array(spans_o).mean(axis=-1), axis=1)
        pos_t = np.expand_dims(np.array(spans_t).mean(axis=-1), axis=0)
        dists = np.absolute(pos_o - pos_t)
        raw_triples: Set[Tuple[int, int, LabelEnum]] = set()

        closest = np.argmin(dists, axis=1)
        for i, span in enumerate(spans_o):
            raw_triples.add((i, int(closest[i]), opinion_to_label[span]))
        closest = np.argmin(dists, axis=0)
        for i, span in enumerate(spans_t):
            raw_triples.add((int(closest[i]), i, target_to_label[span]))

        triples = []
        for i, j, label in raw_triples:
            os, oe = spans_o[i]
            ts, te = spans_t[j]
            triples.append(
                SentimentTriple(o_start=os, o_end=oe, t_start=ts, t_end=te, label=label)
            )
        return triples


class Sentence(BaseModel):
    tokens: List[str]
    entities: List[Entity]
    weight: int
    id: int
    is_labeled: bool
    triples: List[SentimentTriple]
    spans: List[Tuple[int, int, LabelEnum]] = []

    def extract_spans(self) -> List[Tuple[int, int, str]]:
        spans = []
        for e in self.entities:
            spans.append((e.start, e.end, e.ent_type))
        spans = sorted(set(spans))
        return spans

    @classmethod
    def from_instance(cls, x: LinearInstance):
        sentence = cls(
            tokens=x.input,
            weight=x.weight,
            entities=[Entity.from_raw_entity(o) for o in x.output[0]],
            id=x.instance_id,
            triples=[SentimentTriple.from_raw_triple(o) for o in x.output[1]],
            is_labeled=x.is_labeled,
        )
        assert vars(x) == vars(sentence.to_instance())
        return sentence

    def to_instance(self) -> LinearInstance:
        output = ([t.to_raw_entity() for t in self.entities], [t.to_raw_triple() for t in self.triples])
        instance = LinearInstance(self.id, self.weight, self.tokens, output)
        instance.is_labeled = self.is_labeled
        return instance

    def as_text(self) -> str:
        tokens = list(self.tokens)
        for t in self.triples:
            tokens[t.o_start] = "(" + tokens[t.o_start]
            tokens[t.o_end] = tokens[t.o_end] + ")"
            tokens[t.t_start] = "[" + tokens[t.t_start]
            tokens[t.t_end] = tokens[t.t_end] + "]"
        return " ".join(tokens)


class Data(BaseModel):
    root: Path
    data_split: SplitEnum
    sentences: Optional[List[Sentence]]
    num_instances: int = -1
    opinion_offset: int = 3  # Refer: jet_o.py
    is_labeled: bool = False

    def load(self):
        if self.sentences is None:
            path = self.root / f"{self.data_split}.json"
            instances = TagReader.read_inst(
                file=path,
                is_labeled=self.is_labeled,
                number=self.num_instances,
                opinion_offset=self.opinion_offset,
            )
            self.sentences = [Sentence.from_instance(x) for x in instances]

    def analyze_spans(self):
        print("\nHow often is target closer to opinion than any invalid target?")
        records = []
        for s in self.sentences:
            valid_pairs = set([(a.opinion, a.target) for a in s.triples])
            for a in s.triples:
                closest = None
                for b in s.triples:
                    dist_a = abs(np.mean(a.opinion) - np.mean(a.target))
                    dist_b = abs(np.mean(a.opinion) - np.mean(b.target))
                    if dist_b <= dist_a and (a.opinion, b.target) not in valid_pairs:
                        closest = b.target

                spans = [a.opinion, a.target]
                if closest is not None:
                    spans.append(closest)

                tokens = list(s.tokens)
                for start, end in spans:
                    tokens[start] = "[" + tokens[start]
                    tokens[end] = tokens[end] + "]"

                start = min([s[0] for s in spans])
                end = max([s[1] for s in spans])
                tokens = tokens[start: end + 1]

                records.append(dict(is_closest=closest is None, text=" ".join(tokens)))
        df = pd.DataFrame(records)
        print(df["is_closest"].mean())
        print(df[~df["is_closest"]].head())

    def analyze_joined_spans(self):
        print("\nHow often are target/opinion spans joined?")
        join_targets = 0
        join_opinions = 0
        total_targets = 0
        total_opinions = 0

        for s in self.sentences:
            targets = set([t.target for t in s.triples])
            opinions = set([t.opinion for t in s.triples])
            total_targets += len(targets)
            total_opinions += len(opinions)
            join_targets += count_joins(targets)
            join_opinions += count_joins(opinions)

        print(
            dict(
                targets=join_targets / total_targets,
                opinions=join_opinions / total_opinions,
            )
        )

    def analyze_tag_counts(self):
        print("\nHow many tokens are target/opinion/none?")
        record = []
        for s in self.sentences:
            tags = [str(None) for _ in s.tokens]
            for t in s.triples:
                for i in range(t.o_start, t.o_end + 1):
                    tags[i] = "Opinion"
                for i in range(t.t_start, t.t_end + 1):
                    tags[i] = "Target"
            record.extend(tags)
        print({k: v / len(record) for k, v in Counter(record).items()})

    def analyze_span_distance(self):
        print("\nHow far is the target/opinion from each other on average?")
        distances = []
        for s in self.sentences:
            for t in s.triples:
                x_opinion = (t.o_start + t.o_end) / 2
                x_target = (t.t_start + t.t_end) / 2
                distances.append(abs(x_opinion - x_target))
        print(get_simple_stats(distances))

    def analyze_opinion_labels(self):
        print("\nFor opinion/target how often is it associated with only 1 polarity?")
        for key in ["opinion", "target"]:
            records = []
            for s in self.sentences:
                term_to_labels: Dict[Tuple[int, int], List[LabelEnum]] = {}
                for t in s.triples:
                    term_to_labels.setdefault(getattr(t, key), []).append(t.label)
                records.extend([len(set(labels)) for labels in term_to_labels.values()])
            is_single_label = [n == 1 for n in records]
            print(
                dict(
                    key=key,
                    is_single_label=sum(is_single_label) / len(is_single_label),
                    stats=get_simple_stats(records),
                )
            )

    def analyze_tag_score(self):
        print("\nIf have all target and opinion terms (unpaired), what is max f_score?")
        pred = copy.deepcopy(self.sentences)
        for s in pred:
            target_to_label = {t.target: t.label for t in s.triples}
            opinion_to_label = {t.opinion: t.label for t in s.triples}
            s.triples = TripleHeuristic().run(opinion_to_label, target_to_label)

        analyzer = ResultAnalyzer()
        analyzer.run(pred, gold=self.sentences, print_limit=0)

    def analyze_pos_patterns(self):
        print("\nCan we use POS patterns to extract triples?")
        sents = self.sentences[:1000]
        tagger = PosTagger()
        s: Sentence

        tags = tagger.run([s.tokens for s in sents])
        s_train, s_dev, tags_train, tags_dev = train_test_split(
            sents, tags, test_size=0.2, random_state=42
        )
        patterns: Set[Tuple[str, ...]] = set()
        for s, tags in zip(s_train, tags_train):
            for t in s.triples:
                start = min(t.t_start, t.o_start)
                end = max(t.t_end, t.o_end)
                assert len(s.tokens) == len(tags)
                _tokens = s.tokens[start: end + 1]
                _tags = tags[start: end + 1]
                patterns.add(tuple(_tags))

        patterns_dev: Set[Tuple[str, ...]] = set()
        for s, tags in zip(s_dev, tags_dev):
            for t in s.triples:
                start = min(t.t_start, t.o_start)
                end = max(t.t_end, t.o_end)
                assert len(s.tokens) == len(tags)
                _tokens = s.tokens[start: end + 1]
                _tags = tags[start: end + 1]
                patterns_dev.add(tuple(_tags))

        print(
            dict(
                triples=len([t for s in sents for t in s.triples]),
                patterns=len(patterns),
                patterns_dev=len(patterns_dev),
                overlap=len(patterns.intersection(patterns_dev)),
            )
        )

    def analyze_ner(self):
        print("\n How many opinion/target per sentence?")
        num_o, num_t = [], []
        for s in self.sentences:
            opinions, targets = set(), set()
            for t in s.triples:
                opinions.add((t.o_start, t.o_end))
                targets.add((t.t_start, t.t_end))
            num_o.append(len(opinions))
            num_t.append(len(targets))
        print(
            dict(
                num_o=get_simple_stats(num_o),
                num_t=get_simple_stats(num_t),
                sentences=len(self.sentences),
            )
        )

    def analyze_direction(self):
        print("\n For targets, is opinion offset always positive/negative/both?")
        records = []
        for s in self.sentences:
            span_to_offsets = {}
            for t in s.triples:
                off = np.mean(t.target) - np.mean(t.opinion)
                span_to_offsets.setdefault(t.opinion, []).append(off)
            for span, offsets in span_to_offsets.items():
                labels = [
                    LabelEnum.positive if off > 0 else LabelEnum.negative
                    for off in offsets
                ]
                lab = labels[0] if len(set(labels)) == 1 else LabelEnum.neutral
                records.append(
                    dict(
                        span=" ".join(s.tokens[span[0]: span[1] + 1]),
                        text=s.as_text(),
                        offsets=lab,
                    )
                )
        df = pd.DataFrame(records)
        print(df["offsets"].value_counts(normalize=True))
        df = df[df["offsets"] == LabelEnum.neutral].drop(columns=["offsets"])
        with pd.option_context("display.max_colwidth", 999):
            print(df.head())

    def analyze(self):
        triples = [t for s in self.sentences for t in s.triples]
        info = dict(
            root=self.root,
            sentences=len(self.sentences),
            sentiments=Counter([t.label for t in triples]),
            target_lengths=get_simple_stats(
                [abs(t.t_start - t.t_end) + 1 for t in triples]
            ),
            opinion_lengths=get_simple_stats(
                [abs(t.o_start - t.o_end) + 1 for t in triples]
            ),
            sentence_lengths=get_simple_stats([len(s.tokens) for s in self.sentences]),
        )
        for k, v in info.items():
            print(k, v)

        self.analyze_direction()
        self.analyze_ner()
        self.analyze_spans()
        self.analyze_joined_spans()
        self.analyze_tag_counts()
        self.analyze_span_distance()
        self.analyze_opinion_labels()
        self.analyze_tag_score()
        self.analyze_pos_patterns()
        print("#" * 80)


def merge_data(items: List[Data]) -> Data:
    merged = Data(root=Path(), data_split=items[0].data_split, sentences=[])
    for data in items:
        data.load()
        merged.sentences.extend(data.sentences)
    return merged


class Result(BaseModel):
    num_sentences: int
    num_pred: int = 0
    num_gold: int = 0
    num_correct: int = 0
    num_start_correct: int = 0
    num_start_end_correct: int = 0
    num_opinion_correct: int = 0
    num_target_correct: int = 0
    num_span_overlap: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f_score: float = 0.0


class ResultAnalyzer(BaseModel):
    @staticmethod
    def check_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        return (b_start <= a_start <= b_end) or (b_start <= a_end <= b_end)

    @staticmethod
    def run_sentence(pred: Sentence, gold: Sentence):
        assert pred.tokens == gold.tokens
        triples_gold = set([t.as_text(gold.tokens) for t in gold.triples])
        triples_pred = set([t.as_text(pred.tokens) for t in pred.triples])
        tp = triples_pred.intersection(triples_gold)
        fp = triples_pred.difference(triples_gold)
        fn = triples_gold.difference(triples_pred)
        if fp or fn:
            print(dict(gold=gold.as_text()))
            print(dict(pred=pred.as_text()))
            print(dict(tp=tp))
            print(dict(fp=fp))
            print(dict(fn=fn))
            print("#" * 80)

    @staticmethod
    def analyze_labels(pred: List[Sentence], gold: List[Sentence]):
        y_pred = []
        y_gold = []
        for i in range(len(pred)):
            for p in pred[i].triples:
                for g in gold[i].triples:
                    if (p.opinion, p.target) == (g.opinion, g.target):
                        y_pred.append(str(p.label))
                        y_gold.append(str(g.label))

        print(dict(num_span_correct=len(y_pred)))
        if y_pred:
            print(classification_report(y_gold, y_pred))

    @staticmethod
    def analyze_spans(pred: List[Sentence], gold: List[Sentence]):
        num_triples_gold, triples_found_o, triples_found_t = 0, set(), set()
        for label in [LabelEnum.opinion, LabelEnum.target]:
            num_correct, num_pred, num_gold = 0, 0, 0
            is_target = {LabelEnum.opinion: False, LabelEnum.target: True}[label]
            for i, (p, g) in enumerate(zip(pred, gold)):
                spans_gold = set(g.spans if g.spans else g.extract_spans())
                spans_pred = set(p.spans if p.spans else p.extract_spans())
                spans_gold = set([s for s in spans_gold if s[-1] == label])
                spans_pred = set([s for s in spans_pred if s[-1] == label])

                num_gold += len(spans_gold)
                num_pred += len(spans_pred)
                num_correct += len(spans_gold.intersection(spans_pred))

                for t in g.triples:
                    num_triples_gold += 1
                    span = (t.target if is_target else t.opinion) + (label,)
                    if span in spans_pred:
                        t_unique = (i,) + tuple(t.dict().items())
                        if is_target:
                            triples_found_t.add(t_unique)
                        else:
                            triples_found_o.add(t_unique)

            if num_correct and num_pred and num_gold:
                p = round(num_correct / num_pred, ndigits=4)
                r = round(num_correct / num_gold, ndigits=4)
                f = round(2 * p * r / (p + r), ndigits=4)
                info = dict(label=label, p=p, r=r, f=f)
                print(json.dumps(info, indent=2))

        assert num_triples_gold % 2 == 0  # Was double-counted above
        num_triples_gold = num_triples_gold // 2
        num_triples_pred_ceiling = len(triples_found_o.intersection(triples_found_t))
        triples_pred_recall_ceiling = num_triples_pred_ceiling / num_triples_gold
        print("\n What is the upper bound for RE from predicted O & T?")
        print(dict(recall=round(triples_pred_recall_ceiling, ndigits=4)))

    @classmethod
    def run(cls, pred: List[Sentence], gold: List[Sentence], print_limit=16):
        assert len(pred) == len(gold)
        cls.analyze_labels(pred, gold)

        r = Result(num_sentences=len(pred))
        for i in range(len(pred)):
            if i < print_limit:
                cls.run_sentence(pred[i], gold[i])
            r.num_pred += len(pred[i].triples)
            r.num_gold += len(gold[i].triples)
            for p in pred[i].triples:
                for g in gold[i].triples:
                    if p.dict() == g.dict():
                        r.num_correct += 1
                    if (p.o_start, p.t_start) == (g.o_start, g.t_start):
                        r.num_start_correct += 1
                    if (p.opinion, p.target) == (g.opinion, g.target):
                        r.num_start_end_correct += 1
                    if p.opinion == g.opinion:
                        r.num_opinion_correct += 1
                    if p.target == g.target:
                        r.num_target_correct += 1
                    if cls.check_overlap(*p.opinion, *g.opinion) and cls.check_overlap(
                            *p.target, *g.target
                    ):
                        r.num_span_overlap += 1

        e = 1e-9
        r.precision = round(r.num_correct / (r.num_pred + e), 4)
        r.recall = round(r.num_correct / (r.num_gold + e), 4)
        r.f_score = round(2 * r.precision * r.recall / (r.precision + r.recall + e), 3)
        print(r.json(indent=2))
        cls.analyze_spans(pred, gold)


def test_aste(root="aste/data/triplet_data"):
    for folder in Path(root).iterdir():
        scorer = nereval()
        data = Data(root=folder, data_split=SplitEnum.train)
        data.load()
        data.analyze()

        instances = [s.to_instance() for s in data.sentences]
        for i in instances:
            i.set_prediction(i.output)
        print(dict(score=str(scorer.eval(instances))))
        print(SentimentTriple.from_raw_triple(instances[0].output[1][0]))


def test_merge(root="aste/data/triplet_data"):
    unmerged = [Data(root=p, data_split=SplitEnum.train) for p in Path(root).iterdir()]
    data = merge_data(unmerged)
    data.analyze()


if __name__ == "__main__":
    # test_aste()
    test_merge()
    # test_parser()