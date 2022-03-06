import tvm._ffi
import json
import heapq
import numpy as np
from tvm.runtime import Object
from ..capsules import ComputeDAG
from .intrin_match import IntrinMatchResult
from .. import _ffi_api
from ..search import CDParamGenerator, Entry, SAEntryGenerator
from ..utils import bi_product, substitute_inputs, softmax
from functools import reduce


@tvm._ffi.register_object("auto_tensorize.TransformState")
class TransformState(Object):
    """
    Args:
    ---
    main_op_map Map for main op
    elem_op_map Map for elementwise op
    axis_map Map for axis
    reverse_axis_map Reverse map for axis
    target_dag Target compute dag
    intrin_dag Intrin compute dag
    """

    def __init__(self, main_op_map, elem_op_map, axis_map, target_dag, intrin_dag):
        self.__init_handle_by_constructor__(
            _ffi_api.TransformState, main_op_map, elem_op_map, axis_map, target_dag, intrin_dag
        )


@tvm._ffi.register_object("auto_tensorize.TransformRequest")
class TransformRequest(Object):
    """
    Args:
    ---
    name:
    axis_map:
    reverse_axis_map:
    time_loops:
    """

    def __init__(
        self,
        name,
        axis_map,
        reverse_axis_map,
        space_loops,
        time_loops,
        padding=False,
        drop_output=False,
    ):
        self.__init_handle_by_constructor__(
            _ffi_api.TransformRequest,
            name,
            axis_map,
            reverse_axis_map,
            space_loops,
            time_loops,
            padding,
            drop_output,
        )

    def __str__(self):
        ret = "TransformRequest("
        ret += str(self.name) + ",\n"
        ret += "\tAxis Map:\n"
        for k, v in self.axis_map.items():
            ret += "\t\t" + str(k) + ":" + str(v) + "\n"
        ret += "\tReverse Axis Map:\n"
        for k, v in self.reverse_axis_map.items():
            ret += "\t\t" + str(k) + ":" + str(v) + "\n"
        ret += "\tSpace Loops:\n\t\t" + "\n\t\t".join([str(x) for x in self.space_loops])
        ret += "\n\tTime Loops:\n\t\t" + "\n\t\t".join([str(x) for x in self.time_loops])
        ret += "\n\tPadding: " + str(self.need_padding)
        ret += "\n\tDrop Output " + str(self.drop_output)
        ret += ")\n"
        return ret


def infer_range(vars_to_infer, original_vars, original_range_map):
    """Infer ranges for expressions

    Parameters
    ----------
    vars_to_infer:
    original_vars:
    original_range_map:

    Returns
    -------

    """
    range_map = _ffi_api.InferRange(vars_to_infer, original_vars, original_range_map)
    return range_map


def transform_main_op(init, request):
    """Infer ranges for expressions

    Parameters
    ----------
    init:
    request:

    Returns
    -------

    """
    n = _ffi_api.TransformMainOp(init, request)
    return n


class UnfoldChoiceGenerator(CDParamGenerator):
    def __init__(self, axis_map, unify=True):
        num_items = 0
        keys = []
        values = []
        for k, lst in axis_map.items():
            tmp_num_items = len(lst)
            if num_items:
                assert num_items == tmp_num_items
            num_items = tmp_num_items
            keys.append(k)
            values.append(lst)

        tuples = list(zip(*values))

        def unify_helper(bit_vec, tuples, visited, results):
            super_tuple = []
            num_key = len(tuples[0])
            for i, bit in enumerate(bit_vec):
                if bit:
                    super_tuple.append(tuples[i])
            if not super_tuple:
                return
            merged_tuple = []
            for i in range(num_key):
                tmp = set()
                for v in super_tuple:
                    tmp.add(str(v[i].var.name))
                merged_tuple.append(tuple(sorted(list(tmp))))
            merged_tuple = tuple(merged_tuple)

            if merged_tuple in visited:
                return
            else:
                visited.add(merged_tuple)
                results.append(bit_vec)

        if unify and num_items <= 10:
            # when just a few choices

            unfolds = bi_product(num_items)
            visited = set()
            unified_unfolds = []
            for bit_vec in unfolds:
                unify_helper(bit_vec, tuples, visited, unified_unfolds)

            self.unfolds = unified_unfolds
        elif unify:
            # when too many choices
            # we mainly consider two kinds of choices:
            # 1. single item choice
            # 2. all items

            unfolds = []
            for i, item in enumerate(tuples):
                choose = True
                for a, b in zip(item, keys):
                    if int(a.dom.extent) != int(b.dom.extent):
                        choose = False
                        break
                if choose:
                    unfolds.append([1 if j == i else 0 for j in range(num_items)])
            if not unfolds:
                unfolds = [[1 if j == i else 0 for j in range(num_items)] for i in range(num_items)]
            unfolds.append([1 for i in range(num_items)])
            visited = set()
            unified_unfolds = []
            for bit_vec in unfolds:
                unify_helper(bit_vec, tuples, visited, unified_unfolds)
            self.unfolds = unified_unfolds
        else:
            self.unfolds = bi_product(num_items)
        print(f"Totally {len(self.unfolds)} different mappings for this matching", flush=True)
        # self.unfolds = [[1 for _ in range(num_items)]]
        self.choices = list(range(len(self.unfolds)))
        self.reverse_map = {self.to_hashable(k): v for v, k in enumerate(self.unfolds)}

        self.directions = [1, 0, -1]
        # self.directions = [0]
        self.init_Q_table()

    def map_to_hidden(self, factors):
        return self.reverse_map[self.to_hashable(factors)]

    def map_from_hidden(self, init):
        return self.unfolds[init]

    def move_towards_direction(self, init, d):
        des = init + d
        return des

    def valid(self, init):
        return 0 <= init < len(self.choices)

    # def get_random_direction(self, init):
    #     one_choice = None
    #     zero_choice = None
    #     neg_one_choice = None
    #     for d, (des, q_value) in self.Q_table[self.to_hashable(init)].items():
    #         if d == 0:
    #             zero_choice = (d, des)
    #         elif d == 1:
    #             one_choice = (d, des)
    #         elif d == -1:
    #             neg_one_choice = (d, des)
    #     random = np.random.random()
    #     if random < 0.2:
    #         return one_choice if one_choice is not None else zero_choice
    #     if random > 0.8:
    #         return neg_one_choice if neg_one_choice is not None else zero_choice
    #     return zero_choice


class Record(object):
    def __init__(self, unfold_choice):
        # choice = (des, direction)
        self.unfold_choice = unfold_choice

    def to_json(self):
        return {"unfold": self.unfold_choice}

    def as_key(self):
        return "(" + ",".join(map(str, self.unfold_choice[0])) + ")"

    def __str__(self):
        return json.dumps(self.to_json())


class MappingGenerator(SAEntryGenerator):
    def __init__(
        self,
        intrin_match_result,
        eps=1e-1,
        log_file="transform_schedule_generator.log",
        steps=1,
        allow_repeat=False,
        topk=3,
    ):
        super(MappingGenerator, self).__init__(
            eps, Record, steps=steps, log_file=log_file, allow_repeat=allow_repeat, topk=topk
        )
        self.init_param_generator(intrin_match_result)
        self.init_score_table()

    def init_param_generator(self, intrin_match_result):
        assert isinstance(intrin_match_result, IntrinMatchResult)
        # match_point_num = -1
        # for k, v in intrin_match_result.axis_map.items():
        #     match_len = len(v)
        #     if match_point_num < 0:
        #         match_point_num = match_len
        #     assert match_point_num == match_len
        # self.unfold_gen = UnfoldChoiceGenerator(match_point_num)
        self.unfold_gen = UnfoldChoiceGenerator(intrin_match_result.axis_map)
        self.generator_lst = [self.unfold_gen]

    def init_score_table(self):
        self.score_table = softmax([0.5 for gen in self.generator_lst])

    def get_generators(self):
        return self.generator_lst

    def record_from_json(self, obj):
        return self.record_cls(obj["unfold"])

    def get_all(self):
        ret = []
        for unfold in self.unfold_gen.get_all():
            ret.append(self.record_cls(unfold))
        return ret

    def get_record(self, entry=None, policy="random"):
        if entry is None:
            return self.record_cls(self.unfold_gen.get(policy=policy))
        else:
            return self.record_cls(
                self.unfold_gen.get(hint=entry.record.unfold_choice[0], policy=policy)
            )

    def get_records_mutate_one_generator(self, record, to_mutate, steps):
        unfold = record.unfold_choice

        next_unfold = self.unfold_gen.get_next(unfold[0], to_mutate)

        has_mutate = False

        def helper(_gen, org_val):
            nonlocal has_mutate
            try:
                ret = next(_gen)
                has_mutate = True
            except StopIteration:
                ret = org_val
            return ret

        for s in range(steps):
            unfold = helper(next_unfold, unfold)
            if has_mutate:
                yield self.record_cls(unfold)
            has_mutate = False

    def feedback_value(self, entry, value):
        self.unfold_gen.feedback(*entry.record.unfold_choice, value)


class MappingApplier(object):
    def __init__(self, intrin_match_result, verbose=False):
        assert isinstance(intrin_match_result, IntrinMatchResult)
        self.init_state = TransformState(
            intrin_match_result.main_op_map,
            intrin_match_result.elem_op_map,
            intrin_match_result.axis_map,
            intrin_match_result.target_dag,
            intrin_match_result.intrin_dag,
        )
        self.verbose = verbose

    def apply_unfold(self, record, state, drop_output=False):
        # unfold
        intrin_main_op = None
        target_main_op = None
        for k, v in state.main_op_map.items():
            intrin_main_op = k
            target_main_op = v
        assert intrin_main_op is not None
        assert target_main_op is not None
        intrin_axis = list(intrin_main_op.axis) + list(intrin_main_op.reduce_axis)
        # make sure everytime the unfold axes are the same order
        intrin_axis_pos = {}
        for i, axis in enumerate(intrin_axis):
            intrin_axis_pos[axis] = i
        target_axis = list(target_main_op.axis) + list(target_main_op.reduce_axis)
        unfold_choice = record.unfold_choice[0]
        choices = []
        tmp = []
        filtered_intrin_axis = []
        for axis in intrin_axis:
            if len(state.axis_map[axis]) > 0:
                tmp.append(state.axis_map[axis])
                filtered_intrin_axis.append(axis)
        intrin_axis = filtered_intrin_axis
        tmp = list(zip(*tmp))
        for i, v in enumerate(unfold_choice):
            if v == 1:
                choices.append(tmp[i])
        choices = list(zip(*choices))

        name = ".unfold"
        fwd_axis_map = {}
        rvs_axis_map = {}
        space_loops = []
        time_loops = []

        def flatten(axes, strides):
            ret = 0
            for a, s in zip(axes, strides):
                ret = ret + a * s
            return ret

        def sort_axis(axes):
            return sorted(axes, key=lambda x: intrin_axis_pos[x] if x in intrin_axis_pos else 0)

        for axis, choice in zip(intrin_axis, choices):
            # sort axes according to original positions
            choice = sort_axis(choice)
            visited = set()
            unique_choice = []
            for c in choice:
                if c not in visited:
                    unique_choice.append(c)
                    visited.add(c)
            unique_stride = []
            stride = 1
            for c in reversed(unique_choice):
                unique_stride.append(stride)
                stride *= int(c.dom.extent)
            unique_stride = list(reversed(unique_stride))
            fwd_axis_map[axis] = flatten(unique_choice, unique_stride)
            for i, (a, s) in enumerate(zip(unique_choice, unique_stride)):
                if i > 0:
                    rvs_axis_map[a] = axis % unique_stride[i - 1] // s
                else:
                    rvs_axis_map[a] = axis // s
            space_loops.extend(unique_choice)

        visited = set()
        for axis in space_loops:
            visited.add(axis)

        for axis in target_axis:
            if axis not in visited:
                time_loops.append(axis)

        request = TransformRequest(
            name, fwd_axis_map, rvs_axis_map, space_loops, time_loops, drop_output=drop_output
        )
        if self.verbose:
            print(str(request), flush=True)
        unfold_state = transform_main_op(state, request)
        return unfold_state

    def apply_fold(self, record, state, drop_output=False):
        # fold
        intrin_main_op = None
        target_main_op = None
        for k, v in state.main_op_map.items():
            intrin_main_op = k
            target_main_op = v
        assert intrin_main_op is not None
        assert target_main_op is not None
        intrin_axis = list(intrin_main_op.axis) + list(intrin_main_op.reduce_axis)
        target_axis = list(target_main_op.axis) + list(target_main_op.reduce_axis)

        choices = []
        tmp = []
        filtered_intrin_axis = []
        for axis in intrin_axis:
            # TODO: ues a better way to filter out unrelated axis
            if int(axis.dom.extent) > 1:
                tmp.append(state.axis_map[axis][-1])
                filtered_intrin_axis.append(axis)
        intrin_axis = filtered_intrin_axis
        choices = list(tmp)

        name = ".fold"
        fwd_axis_map = {}
        rvs_axis_map = {}
        space_loops = []
        time_loops = []
        need_padding = False

        for axis, choice in zip(intrin_axis, choices):
            factor = int(axis.dom.extent)
            extent = int(choice.dom.extent)
            if extent < factor:
                raise RuntimeError(
                    (
                        "\nThis mapping is infeasible because it attempts to split a"
                        f"larger dim of extent {factor} from a smaller dim of extent {extent},"
                        "which will result in incorrect code in TVM."
                        "\nThis is an internal bug of TVM and should be addressed by the official TVM group.\n"
                    )
                )
            outer = (extent + factor - 1) // factor
            var = tvm.tir.IterVar([0, outer], axis.var.name + ".o", axis.iter_type)
            fwd_axis_map[axis] = choice % factor
            fwd_axis_map[var] = choice // factor
            rvs_axis_map[choice] = var * factor + axis
            space_loops.append(choice)
            time_loops.append(var)
            if extent < factor:
                need_padding = True

        visited = set()
        for axis in space_loops:
            visited.add(axis)

        for axis in target_axis:
            if axis not in visited:
                time_loops.append(axis)

        request = TransformRequest(
            name,
            fwd_axis_map,
            rvs_axis_map,
            space_loops,
            time_loops,
            padding=need_padding,
            drop_output=drop_output,
        )
        if self.verbose:
            print(str(request), flush=True)
        fold_state = transform_main_op(state, request)
        return fold_state

    def apply(self, record, drop_output=False):
        state = self.apply_unfold(record, self.init_state, drop_output)
        state = self.apply_fold(record, state, drop_output)
        return state
