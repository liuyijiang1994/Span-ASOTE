This project optimizes [Span-ASTE](https://github.com/chiayewken/Span-ASTE) for [ASOTE](https://github.com/l294265421/ASOTE) task to support sentences without any relation.

Reference:

[Span-ASTE](https://github.com/chiayewken/Span-ASTE): Learning Span-Level Interactions for Aspect Sentiment Triplet Extraction

[ASOTE](https://github.com/l294265421/ASOTE): A More Fine-Grained Aspect-Sentiment-Opinion Triplet Extraction Task

### Usage

- Install data and requirements: `bash setup.sh`
- Run training and evaluation on GPU 0: `bash aste/main.sh 0`
- Training config (10 epochs): [training_config/aste.jsonnet](training_config/aste.jsonnet)
- Modified data reader: [span_model/data/dataset_readers/span_model.py](span_model/data/dataset_readers/span_model.py)
- Modeling code: [span_model/models/span_model.py](span_model/models/span_model.py)
