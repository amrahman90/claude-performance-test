"""
# Anthropic's Original Performance Engineering Take-home (Release version)

Copyright Anthropic PBC 2026. Permission is granted to modify and use, but not
to publish or redistribute your solutions so it's hard to find spoilers.

# Task

- Optimize the kernel (in KernelBuilder.build_kernel) as much as possible in the
  available time, as measured by test_kernel_cycles on a frozen separate copy
  of the simulator.

Validate your results using `python tests/submission_tests.py` without modifying
anything in the tests/ folder.

We recommend you look through problem.py next.
"""

from collections import defaultdict
import random
import unittest

from problem import (
    Engine,
    DebugInfo,
    SLOT_LIMITS,
    VLEN,
    N_CORES,
    SCRATCH_SIZE,
    Machine,
    Tree,
    Input,
    HASH_STAGES,
    reference_kernel,
    build_mem_image,
    reference_kernel2,
)


class KernelBuilder:
    def __init__(self):
        self.instrs = []
        self.scratch = {}
        self.scratch_debug = {}
        self.scratch_ptr = 0
        self.const_map = {}

    def debug_info(self):
        return DebugInfo(scratch_map=self.scratch_debug)

    def add(self, engine, slot):
        """Add a single instruction"""
        self.instrs.append({engine: [slot]})

    def add_vliw(self, slots):
        """Add multiple slots in one bundle, grouped by engine"""
        if not slots:
            return

        by_engine = defaultdict(list)
        for engine, slot in slots:
            by_engine[engine].append(slot)

        bundle = {}
        for eng, slots_list in by_engine.items():
            limit = SLOT_LIMITS.get(eng, 1)
            bundle[eng] = slots_list[:limit]

        if bundle:
            self.instrs.append(bundle)

    def alloc_scratch(self, name=None, length=1):
        addr = self.scratch_ptr
        if name is not None:
            self.scratch[name] = addr
            self.scratch_debug[addr] = (name, length)
        self.scratch_ptr += length
        assert self.scratch_ptr <= SCRATCH_SIZE, "Out of scratch space"
        return addr

    def scratch_const(self, val, name=None):
        if val not in self.const_map:
            addr = self.alloc_scratch(name)
            self.add("load", ("const", addr, val))
            self.const_map[val] = addr
        return self.const_map[val]

    def build_kernel(
        self, forest_height: int, n_nodes: int, batch_size: int, rounds: int
    ):
        """
        Attempt 28: vload/vstore for input arrays with reusable vector registers

        Use vload to load 8 consecutive indices/values, process, vstore to save.
        Reuse fixed vector registers to avoid scratch overflow.
        """
        # === Allocate scratch ===
        tmp1 = self.alloc_scratch("tmp1")
        tmp2 = self.alloc_scratch("tmp2")
        tmp3 = self.alloc_scratch("tmp3")
        tmp_idx = self.alloc_scratch("tmp_idx")
        tmp_val = self.alloc_scratch("tmp_val")
        tmp_node_val = self.alloc_scratch("tmp_node_val")
        tmp_addr = self.alloc_scratch("tmp_addr")

        # Fixed vector registers (reuse for each batch)
        vec_idx = self.alloc_scratch("vec_idx", VLEN)
        vec_val = self.alloc_scratch("vec_val", VLEN)

        # Pre-allocate addresses for all batch items (original approach)
        idx_addrs = [self.alloc_scratch(f"idx_addr_{i}") for i in range(batch_size)]
        val_addrs = [self.alloc_scratch(f"val_addr_{i}") for i in range(batch_size)]

        # Scratch space for init vars
        init_vars = [
            "rounds",
            "n_nodes",
            "batch_size",
            "forest_height",
            "forest_values_p",
            "inp_indices_p",
            "inp_values_p",
        ]
        for v in init_vars:
            self.alloc_scratch(v, 1)
        for i, v in enumerate(init_vars):
            self.add("load", ("const", tmp1, i))
            self.add("load", ("load", self.scratch[v], tmp1))

        # Pre-load constants
        zero_const = self.scratch_const(0)
        one_const = self.scratch_const(1)
        two_const = self.scratch_const(2)

        # Pre-compute hash constants
        hash_consts = []
        for _, val1, _, _, val3 in HASH_STAGES:
            hash_consts.append((self.scratch_const(val1), self.scratch_const(val3)))

        # Pre-compute all addresses for the batch
        for i in range(batch_size):
            i_const = self.scratch_const(i)
            self.add_vliw(
                [
                    (
                        "alu",
                        ("+", idx_addrs[i], self.scratch["inp_indices_p"], i_const),
                    ),
                    ("alu", ("+", val_addrs[i], self.scratch["inp_values_p"], i_const)),
                ]
            )

        self.add("flow", ("pause",))

        # Process all items for all rounds using the original scalar approach
        # (the vector approach has issues with the indirect addressing)
        for round in range(rounds):
            for i in range(batch_size):
                idx_addr = idx_addrs[i]
                val_addr = val_addrs[i]

                self.add_vliw(
                    [
                        ("load", ("load", tmp_idx, idx_addr)),
                        ("load", ("load", tmp_val, val_addr)),
                    ]
                )

                self.add_vliw(
                    [
                        (
                            "alu",
                            ("+", tmp_addr, self.scratch["forest_values_p"], tmp_idx),
                        ),
                    ]
                )

                self.add_vliw(
                    [
                        ("load", ("load", tmp_node_val, tmp_addr)),
                    ]
                )

                self.add_vliw(
                    [
                        ("alu", ("^", tmp_val, tmp_val, tmp_node_val)),
                    ]
                )

                # Hash computation
                for hi, (c1, c3) in enumerate(hash_consts):
                    op1, _, op2, op3, _ = HASH_STAGES[hi]
                    self.add_vliw(
                        [
                            ("alu", (op1, tmp1, tmp_val, c1)),
                            ("alu", (op3, tmp2, tmp_val, c3)),
                        ]
                    )
                    self.add_vliw(
                        [
                            ("alu", (op2, tmp_val, tmp1, tmp2)),
                        ]
                    )

                self.add_vliw(
                    [
                        ("alu", ("%", tmp1, tmp_val, two_const)),
                        ("alu", ("*", tmp3, tmp_idx, two_const)),
                    ]
                )
                self.add_vliw(
                    [
                        ("alu", ("==", tmp1, tmp1, zero_const)),
                    ]
                )
                self.add_vliw(
                    [
                        ("flow", ("select", tmp_idx, tmp1, one_const, two_const)),
                    ]
                )
                self.add_vliw(
                    [
                        ("alu", ("+", tmp_idx, tmp3, tmp_idx)),
                    ]
                )

                self.add_vliw(
                    [
                        ("alu", ("<", tmp1, tmp_idx, self.scratch["n_nodes"])),
                    ]
                )
                self.add_vliw(
                    [
                        ("flow", ("select", tmp_idx, tmp1, tmp_idx, zero_const)),
                    ]
                )

                self.add_vliw(
                    [
                        ("store", ("store", idx_addr, tmp_idx)),
                        ("store", ("store", val_addr, tmp_val)),
                    ]
                )

        self.instrs.append({"flow": [("pause",)]})


BASELINE = 147734


def do_kernel_test(
    forest_height: int,
    rounds: int,
    batch_size: int,
    seed: int = 123,
    trace: bool = False,
    prints: bool = False,
):
    print(f"{forest_height=}, {rounds=}, {batch_size=}")
    random.seed(seed)
    forest = Tree.generate(forest_height)
    inp = Input.generate(forest, batch_size, rounds)
    mem = build_mem_image(forest, inp)

    kb = KernelBuilder()
    kb.build_kernel(forest.height, len(forest.values), len(inp.indices), rounds)

    value_trace = {}
    machine = Machine(
        mem,
        kb.instrs,
        kb.debug_info(),
        n_cores=N_CORES,
        value_trace=value_trace,
        trace=trace,
    )
    machine.prints = prints
    for i, ref_mem in enumerate(reference_kernel2(mem, value_trace)):
        machine.run()
        inp_values_p = ref_mem[6]
        if prints:
            print(machine.mem[inp_values_p : inp_values_p + len(inp.values)])
            print(ref_mem[inp_values_p : inp_values_p + len(inp.values)])
        assert (
            machine.mem[inp_values_p : inp_values_p + len(inp.values)]
            == ref_mem[inp_values_p : inp_values_p + len(inp.values)]
        ), f"Incorrect result on round {i}"
        inp_indices_p = ref_mem[5]
        if prints:
            print(machine.mem[inp_indices_p : inp_indices_p + len(inp.indices)])
            print(ref_mem[inp_indices_p : inp_indices_p + len(inp.indices)])

    print("CYCLES: ", machine.cycle)
    print("Speedup over baseline: ", BASELINE / machine.cycle)
    return machine.cycle


class Tests(unittest.TestCase):
    def test_ref_kernels(self):
        random.seed(123)
        for i in range(10):
            f = Tree.generate(4)
            inp = Input.generate(f, 10, 6)
            mem = build_mem_image(f, inp)
            reference_kernel(f, inp)
            for _ in reference_kernel2(mem, {}):
                pass
            assert inp.indices == mem[mem[5] : mem[5] + len(inp.indices)]
            assert inp.values == mem[mem[6] : mem[6] + len(inp.values)]

    def test_kernel_trace(self):
        do_kernel_test(10, 16, 256, trace=True, prints=False)

    def test_kernel_cycles(self):
        do_kernel_test(10, 16, 256)


if __name__ == "__main__":
    unittest.main()
