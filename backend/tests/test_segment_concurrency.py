"""Concurrent segment inference over fake sessions; no model or GPU is required."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.services.predict.pipeline.a_onnx_segment import OnnxSegmentPy


class ObservedLock:
    """A real lock that lets a test observe when the second caller tries to enter."""

    def __init__(self, lock):
        self.lock = lock
        self.attempts_lock = threading.Lock()
        self.attempts = 0
        self.second_attempted = threading.Event()

    def __enter__(self):
        with self.attempts_lock:
            self.attempts += 1
            if self.attempts == 2:
                self.second_attempted.set()
        self.lock.acquire()
        return self

    def __exit__(self, *exc):
        self.lock.release()


class FakeBinding:
    def __init__(self, session):
        self.session = session
        self.input = None
        self.output = None

    def bind_ortvalue_input(self, name, value):
        self.input = value
        self.session.bound_inputs.append(value.copy())

    def bind_output(self, *args):
        pass

    def copy_outputs_to_cpu(self):
        if self.session.runs == 1:
            self.session.copy_started.set()
            if not self.session.release_copy.wait(3):
                raise TimeoutError("test did not release the first output copy")
        return [self.output.copy()]


class FakeBindingSession:
    def __init__(self):
        self.bound_inputs = []
        self.used_bindings = []
        self.runs = 0
        self.copy_started = threading.Event()
        self.release_copy = threading.Event()

    def get_providers(self):
        return ["CUDAExecutionProvider"]

    def get_outputs(self):
        return [SimpleNamespace(name="mask")]

    def io_binding(self):
        return FakeBinding(self)

    def run_with_iobinding(self, binding):
        self.used_bindings.append(binding)
        self.runs += 1
        binding.output = binding.input.copy()


class SegmentConcurrencyTests(unittest.TestCase):
    def model(self, session, *, use_iobinding):
        with patch.object(OnnxSegmentPy, "load_model"):
            model = OnnxSegmentPy({"SEGMENT_CONFIG": {"use_iobinding": use_iobinding}})
        model.sess = session
        model.input_name = "image"
        model.output_name = "mask"
        model.input_h = model.input_w = 2
        model._cached_io = None
        model._cached_shape = None
        return model

    def test_cached_binding_cannot_be_rebound_until_outputs_are_copied(self):
        session = FakeBindingSession()
        model = self.model(session, use_iobinding=True)
        observed_lock = ObservedLock(model._io_binding_lock)
        model._io_binding_lock = observed_lock
        images = [np.full((2, 2, 3), value, dtype=np.uint8) for value in (30, 220)]
        expected = [model.preprocess(image) for image in images]

        with patch(
            "app.services.predict.pipeline.a_onnx_segment.ort.OrtValue.ortvalue_from_numpy",
            side_effect=lambda value, *args: value,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(model.infer, images[0])
            try:
                self.assertTrue(
                    session.copy_started.wait(3), "first output copy did not start"
                )
                second = pool.submit(model.infer, images[1])
                self.assertTrue(
                    observed_lock.second_attempted.wait(3),
                    "second call did not reach the binding",
                )
                self.assertEqual(len(session.bound_inputs), 1)
                np.testing.assert_array_equal(session.bound_inputs[0], expected[0])
            finally:
                session.release_copy.set()
            np.testing.assert_array_equal(first.result(timeout=3), expected[0])
            np.testing.assert_array_equal(second.result(timeout=3), expected[1])

        self.assertEqual(session.runs, 2)
        self.assertIs(session.used_bindings[0], session.used_bindings[1])

    def test_cpu_session_runs_remain_concurrent_without_io_binding(self):
        both_running = threading.Barrier(2, timeout=3)

        class CpuSession:
            def get_providers(self):
                return ["CPUExecutionProvider"]

            def run(self, outputs, inputs):
                both_running.wait()
                return [inputs["image"].copy()]

        model = self.model(CpuSession(), use_iobinding=False)
        images = [np.full((2, 2, 3), value, dtype=np.uint8) for value in (50, 200)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(model.infer, image) for image in images]
            for result, image in zip(results, images):
                np.testing.assert_array_equal(
                    result.result(timeout=4), model.preprocess(image)
                )


if __name__ == "__main__":
    unittest.main()
