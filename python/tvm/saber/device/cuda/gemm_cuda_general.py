import tvm
from tvm.ir import transform
from ..base import Operator
from ...utils import ceil
from ...kernel import (
    kernel_gemm_cuda_general_perfect
)
from ..measure import MeasureOptions, evaluate_function, evaluate_schedule


class GemmGeneral(Operator):
    def __init__(self, in_dtype="float32", out_dtype="float32",
                 threadblock_problem_size=[32, 32, 32],
                 warp_problem_size=[32, 32, 8],
                 instruction_problem_size=[4, 4, 8],
                 epilogues=[],
                 split_K=1):
        super(GemmGeneral, self).__init__()
        self.target = "cuda"
        self.in_dtype = in_dtype
        self.out_dtype = out_dtype
        self.threadblock_problem_size = threadblock_problem_size
        self.warp_problem_size = warp_problem_size
        self.instruction_problem_size = instruction_problem_size
        self.epilogues = []
        self.split_K = split_K
        if self.split_K > 1:
            raise RuntimeError("Not support split_K > 1")
        else:
            self.get_context = lambda *_: kernel_gemm_cuda_general_perfect(
                self.threadblock_problem_size,
                self.warp_problem_size,
                self.instruction_problem_size,
                self.epilogues,
                A_dtype=self.in_dtype,
                B_dtype=self.in_dtype,
                C_dtype=self.out_dtype
            )

    def compile(self, dump=False):
        (
            Output,
            (A, B),
            schedule_func,
            Params,
            Vars
        ) = self.get_context()
        sch = tvm.te.create_schedule(Output.op)
        for func in schedule_func:
            func(sch)

        if dump:
            print(tvm.lower(
                sch, [A, B, Output, *Params, *Vars],
                simple_mode=True
            ))

        gemm_func = tvm.build(
            sch, [A, B, Output, *Params, *Vars],
            target=self.target
        )

        return gemm_func

    def evaluate(self, func, M, N, K, measure_opt=MeasureOptions(
            target="cuda", number=10,
            min_repeat_ms=500), new_process=False):
        A = tvm.te.placeholder([K, M], dtype=self.in_dtype)
        B = tvm.te.placeholder([K, N], dtype=self.in_dtype)
        Output = tvm.te.placeholder([M, N], dtype=self.out_dtype)
        args = [A, B, Output]
        var_values = [
            M, N, K,
            ceil(M, self.threadblock_problem_size[0]),
            ceil(N, self.threadblock_problem_size[1]),
            ceil(K, self.threadblock_problem_size[2])
        ]
        return evaluate_function(
            func, args, var_values, measure_opt, new_process=new_process
        )

    def calculate(self, func, A, B, C):
        K, M = A.shape
        _, N = B.shape
        var_values = [
            M, N, K,
            ceil(M, self.threadblock_problem_size[0]),
            ceil(N, self.threadblock_problem_size[1]),
            ceil(K, self.threadblock_problem_size[2])
        ]
        func(A, B, C, *var_values)

    def try_with(self, M, N, K, measure_opt=MeasureOptions(
            target="cuda", number=10,
            min_repeat_ms=80), new_process=False, dump=False):
        (
            Output,
            (A, B),
            schedule_func,
            Params,
            Vars
        ) = self.get_context()
        sch = tvm.te.create_schedule(Output.op)
        args = [A, B, Output]
        for func in schedule_func:
            func(sch)
        A = tvm.te.placeholder([K, M], dtype=self.in_dtype)
        B = tvm.te.placeholder([K, N], dtype=self.in_dtype)
        Output = tvm.te.placeholder([M, N], dtype=self.out_dtype)
        arg_values = [A, B, Output]
        var_values = [
            M, N, K,
            ceil(M, self.threadblock_problem_size[0]),
            ceil(N, self.threadblock_problem_size[1]),
            ceil(K, self.threadblock_problem_size[2])
        ]

        if dump:
            print(tvm.lower(sch, [*args, *Vars], simple_mode=True))
        return evaluate_schedule(
            sch, args, list(Params) + list(Vars), arg_values, var_values, measure_opt, new_process=new_process)