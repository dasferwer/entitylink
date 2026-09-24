import json
from pathlib import Path

from sklearn.metrics import precision_recall_fscore_support

from entitylink.matching import compare

pairs = json.loads((Path(__file__).parent / "labeled_pairs.json").read_text())
expected = [row["duplicate"] for row in pairs]
predicted = [compare(row["left"], row["right"])["decision"] == "review" for row in pairs]
precision, recall, f1, _ = precision_recall_fscore_support(
    expected, predicted, average="binary", zero_division=0
)
print(
    json.dumps(
        {
            "pairs": len(pairs),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "errors": [i for i, (a, b) in enumerate(zip(expected, predicted)) if a != b],
        },
        indent=2,
    )
)
