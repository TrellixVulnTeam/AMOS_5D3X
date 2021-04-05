import tvm
from ...threadblock import (
    threadblock_gemm_mali_general)
from ...utils import index

def kernel_gemm_general(
    threadblock_problem_size,
    warp_problem_size,
    instruction_problem_size,
    epilogues,
    A_dtype="float32",
    B_dtype="float32",
    C_dtype="float32"
):
    M = index("M")
    N = index("N")
    K = index("K")
    Params = [M, N, K]
    A = tvm.te.placeholder([M, K], dtype=A_dtype, name="A")
    B = tvm.te.placeholder([N, K], dtype=B_dtype, name="B")
    (
        Output,
        schedule_func,
        (M1, N1, K1),
        parse_func
    ) = threadblock_gemm_mali_general(
        threadblock_problem_size,
        warp_problem_size,
        instruction_problem_size,
        epilogues,
        A, B,
        C_dtype=C_dtype
    )

    Gemm = tvm.te.compute(
        [M1, threadblock_problem_size[0], N1, threadblock_problem_size[1]],
        lambda m1, m, n1, n:
            parse_func(m1, n1, m, n),
        name="Gemm"
    )

    def schedule_kernel(sch):
        m1, m, n1, n = sch[Gemm].op.axis
        num_threads = (
            (threadblock_problem_size[0] // warp_problem_size[0])
            * (threadblock_problem_size[1] // warp_problem_size[1])
        ) * (
            (warp_problem_size[0] // instruction_problem_size[0])
            * (warp_problem_size[1] // instruction_problem_size[1])
        )
        sch[Gemm].reorder(m1, n1, m, n)
        fused = sch[Gemm].fuse(m, n)
        fused, threads = sch[Gemm].split(fused, factor=num_threads)
        sch[Gemm].bind(threads, tvm.te.thread_axis("threadIdx.x"))
        sch[Gemm].bind(m1, tvm.te.thread_axis("blockIdx.y"))
        sch[Gemm].bind(n1, tvm.te.thread_axis("blockIdx.x"))

    return (
        Gemm,
        [A, B],
        schedule_func + [schedule_kernel],
        Params,
        (M1, N1, K1)
    )


def kernel_gemm_general_perfect(
    threadblock_problem_size,
    warp_problem_size,
    instruction_problem_size,
    epilogues,
    A_dtype="float32",
    B_dtype="float32",
    C_dtype="float32"
):
    M = index("M")
    N = index("N")
    K = index("K")
    Params = [M, N, K]
    A = tvm.te.placeholder([M, K], dtype=A_dtype, name="A")
    B = tvm.te.placeholder([N, K], dtype=B_dtype, name="B")
    (
        Output,
        schedule_func,
        (M1, N1, K1),
        parse_func
    ) = threadblock_gemm_mali_general(
        threadblock_problem_size,
        warp_problem_size,
        instruction_problem_size,
        epilogues,
        A, B,
        C_dtype=C_dtype
    )

    Gemm = tvm.te.compute(
        [M, N],
        lambda m, n:
            parse_func(m, n),
        name="Gemm"
    )

    def schedule_kernel(sch):
        m, n = sch[Gemm].op.axis
        num_threads = (
            (threadblock_problem_size[0] // warp_problem_size[0])
            * (threadblock_problem_size[1] // warp_problem_size[1])
        ) * (
            (warp_problem_size[0] // instruction_problem_size[0])
            * (warp_problem_size[1] // instruction_problem_size[1])
        )
        fused = sch[Gemm].fuse(m, n)
        fused, threads = sch[Gemm].split(fused, factor=num_threads)
        sch[Gemm].bind(threads, tvm.te.thread_axis("threadIdx.x"))
        sch[Gemm].bind(fused, tvm.te.thread_axis("blockIdx.x"))

    return (
        Gemm,
        [A, B],
        schedule_func + [schedule_kernel],
        Params,
        (M1, N1, K1)
    )
