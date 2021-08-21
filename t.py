from enum import Enum


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


print(LabelEnum.as_list())
print(LabelEnum.opinion)
print(type(LabelEnum.opinion))
