from __future__ import annotations

import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def execute(self, requests):
        responses = []
        for request in requests:
            prompt = pb_utils.get_input_tensor_by_name(request, "prompt")
            if prompt is None:
                error = pb_utils.TritonError("Missing required input tensor: prompt")
                responses.append(pb_utils.InferenceResponse(error=error))
                continue

            values = prompt.as_numpy()
            output = pb_utils.Tensor("echo", np.asarray(values, dtype=object))
            responses.append(pb_utils.InferenceResponse(output_tensors=[output]))
        return responses
