import tvm
from ...capsules import *
from ..recipe_base import (
    CompilationRecipe,
    register_recipe
)
from ..recipe_base import InstructionScope


@register_recipe("opencl", "arm_dot_vlen_local")
class arm_dot_vlen_local_char4(CompilationRecipe):
    scope = InstructionScope.thread

    def __init__(self):
        self.capsules = {"arm_dot": arm_dot_vlen_local}
        self.main_capsule_name = "arm_dot"
        self.anchor_point = "arm_dot"
        self.edges = {}
        self.input_dtypes = {}
        self.output_dtypes = {}

    def get_memory_scope_realize(self, dtype, scope, constant_size, attributes):
        """
        dtype: str
            e.g. int8
        scope: str
            e.g. local
        constant_size: int
            size of elements in the buffer
        attributes: dict of {tvm.runtime.String, tvm.tir.StringImm}
            other useful information, e.g., layout/leading dimension length
        ---
        """
        return ["", constant_size]

    def get_capsule_compute_expression_with_shape(self, reduction_len):
        """
        ---
        Returns:
        inputs, outputs: list of tvm.te.tensor.Tensor
            the compute expression can be tracked
            through [output.op.body for output in outputs]
        """
        capsule = self.capsules["arm_dot"]
        return capsule.get_compute_expression(reduction_len)

    def get_name(self):
        return "arm_dot"

    def get_intrinsic(self, reduction_len, capsule_key="arm_dot"):
        capsule_class = self.capsules[capsule_key]
        capsule = capsule_class(self.get_name())
        return capsule.get_intrinsic(L=reduction_len)

    def get_header(self):
        return ""