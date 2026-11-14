"""Probability-space SHAP for the selected multiclass pipeline."""

import numpy as np
import pandas as pd
import shap

from ml.diagnosis.model import INPUTS


def explain(model, training: pd.DataFrame, test: pd.DataFrame, seed: int) -> dict:
    # Encode the mixed DataFrame to numeric indices for the SHAP masker, then
    # decode before calling the fitted pipeline. Each group stays one feature.
    categories = {
        col: list(dict.fromkeys([*training[col], *test[col]]))
        for col in training.select_dtypes(exclude="number")
    }

    def encode(frame):
        encoded = frame.copy()
        for col, values in categories.items():
            encoded[col] = pd.Categorical(frame[col], categories=values).codes
        return encoded.to_numpy(dtype=float)

    def predict(matrix):
        decoded = pd.DataFrame(matrix, columns=list(INPUTS))
        for col, values in categories.items():
            decoded[col] = np.asarray(values)[decoded[col].astype(int)]
        return model.predict_proba(decoded)

    background = training.sample(n=min(32, len(training)), random_state=seed)
    sample = test.sample(n=min(24, len(test)), random_state=seed)
    encoded = encode(sample)
    explainer = shap.PermutationExplainer(predict, encode(background), seed=seed)
    explanation = explainer(encoded, max_evals=5 * (2 * len(INPUTS) + 1), silent=True)
    probabilities = predict(encoded)
    reconstructed = explanation.base_values + explanation.values.sum(axis=1)
    error = float(np.max(np.abs(reconstructed - probabilities)))
    if not np.allclose(reconstructed, probabilities, atol=1e-8, rtol=0):
        raise ValueError("SHAP attributions did not reconstruct the model probabilities")

    index = int(np.argmax(probabilities[0]))
    return {
        "method": "Permutation SHAP",
        "output_space": "probability",
        "background_rows": len(background),
        "sample_rows": len(sample),
        "max_additivity_error": error,
        "mean_absolute_attribution": dict(
            zip(INPUTS, np.abs(explanation.values).mean(axis=(0, 2)).tolist(), strict=True)
        ),
        "example": {
            "predicted_class": str(model.classes_[index]),
            "probability": float(probabilities[0, index]),
            "base_value": float(explanation.base_values[0, index]),
            "inputs": sample.iloc[0].to_dict(),
            "contributions": dict(
                zip(INPUTS, explanation.values[0, :, index].tolist(), strict=True)
            ),
        },
    }
