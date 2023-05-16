# This code is part of Qiskit.
#
# (C) Copyright IBM 2023.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""The Linear Mapper for Bosons."""

from __future__ import annotations
import operator
import warnings

from functools import reduce, lru_cache

import numpy as np

from qiskit.quantum_info import Pauli, SparsePauliOp
from qiskit_nature import settings

from qiskit_nature.second_q.operators import BosonicOp
from .bosonic_mapper import BosonicMapper


class BosonicLinearMapper(BosonicMapper):
    """
   The Linear boson-to-qubit mapping.

   This mapper generates a linear encoding of the Bosonic operator :math:`b_k^\\dagger, b_k` to qubit
   operators (linear combinations of pauli strings).
   In this linear encoding each bosonic mode is represented via :math:`n_k^{max} + 1` qubits, where
   :math:`n_k^{max}` is the truncation of the mode (meaning the number of states used in the expansion of
   the mode, or equivalently the state at which the maximum excitation can take place).
   The mode :math:`|k\rangle` is then mapped to the occupation number vector
   :math:`|0_{n_k^{max}}, 0_{n_k^{max} - 1}, ..., 0_{n_k + 1}, 1_{n_k}, 0_{n_k - 1}, ..., 0_{0_k}\rangle`

   It implements the formula in Section II.C of Reference [1]:

   .. math::
       b_k^\\dagger = \\sum(\\sqrt{n_k + 1} \\sigma_{n_k}^+\\sigma_{n_k + 1}^-)

   from :math:`n_k = 0` to :math:`n_k^max + 1`
   where :math:`n_k^max` is the truncation order (defined by the user).
   In the following implementation, we explicit the operators :math:`\\sigma^+` and :math:`\\sigma^-`
   with the Pauli matrices:

   .. math::
       \\sigma_{n_k}^+ := S_j^+ = 0.5 * (X_j + i*Y_j)\\
       \\sigma_{n_k}^- := S_j^- = 0.5 * (X_j - i*Y_j)

   The length of the qubit register is: ``BosonicOp.num_modes * (BosonicLinearMapper.truncation + 1)``

   References:
       [1] A. Miessen et al., Quantum algorithms for quantum dynamics: A performance study on the
       spin-boson model, Phys. Rev. Research 3, 043212.
       https://link.aps.org/doi/10.1103/PhysRevResearch.3.043212

    """

    def _map_single(
        self, second_q_op: BosonicOp, *, register_length: int | None = None
    ) -> SparsePauliOp:
        """Maps a :class:`~qiskit_nature.second_q.operators.SparseLabelOp` to a``SparsePauliOp``.

        Args:
            second_q_op: the ``SparseLabelOp`` to be mapped.
            register_length: when provided, this will be used to overwrite the ``register_length``
                attribute of the operator being mapped. This is possible because the
                ``register_length`` is considered a lower bound in a ``SparseLabelOp``.

        Returns:
            The qubit operator corresponding to the problem-Hamiltonian in the qubit space.
        """
        if settings.use_pauli_sum_op:
            warnings.warn(
                DeprecationWarning(
                    "As of version 0.6.0 the PauliSumOp is deprecated. We are ignoring this setting"
                    "for this function. Thus the returned object will have type SparsePauliOp"
                )
            )
        if register_length is None:
            register_length = second_q_op.num_modes

        qubit_register_length = register_length * (self.truncation + 1)
        # Create a Pauli operator, which we will fill in this method
        pauli_op: list[SparsePauliOp] = []
        # Then we loop over all the terms of the bosonic operator
        for terms, coeff in second_q_op.terms():
            # Then loop over each term (terms -> List[Tuple[string, int]])
            bos_op_to_pauli_op = SparsePauliOp(["I" * qubit_register_length], coeffs=[1.0])
            for op, idx in terms:
                if op not in ("+", "-"):
                    break
                pauli_expansion: list[SparsePauliOp] = []
                # Now we are dealing with a single bosonic operator. We have to perform the linear mapper
                for n_k in range(self.truncation):
                    prefactor = np.sqrt(n_k + 1) / 4.0
                    # Define the actual index in the qubit register. It is given by n_k plus the shift
                    # due to the mode onto which the operator is acting
                    register_index = n_k + idx * (self.truncation + 1)
                    # Now build the Pauli operators XX, XY, YX, YY, which arise from S_i^+ S_j^-
                    x_x, x_y, y_x, y_y = self._get_ij_pauli_matrix(
                        register_index, qubit_register_length
                    )

                    tmp_op = SparsePauliOp(x_x) + SparsePauliOp(y_y)
                    if op == "+":
                        tmp_op += -1j * SparsePauliOp(x_y) + 1j * SparsePauliOp(y_x)
                    else:
                        tmp_op += +1j * SparsePauliOp(x_y) - 1j * SparsePauliOp(y_x)
                    pauli_expansion.append(prefactor * tmp_op)
                # Add the Pauli expansion for a single n_k to map of the bosonic operator
                bos_op_to_pauli_op = reduce(operator.add, pauli_expansion).compose(
                    bos_op_to_pauli_op
                )
            # Add the map of the single boson op (e.g. +_0) to the map of the full bosonic operator
            pauli_op.append(coeff * reduce(operator.add, bos_op_to_pauli_op.simplify()))

        # return the lookup table for the transformed XYZI operators
        bos_op_encoding = reduce(operator.add, pauli_op)
        return bos_op_encoding

    @classmethod
    @lru_cache(maxsize=32)
    def _get_ij_pauli_matrix(cls, register_index: int, register_length: int):
        """This method builds the Qiskit Pauli operators of the operators XX, YY, XY and YX

        Args:
            register_index (int): the index of the qubit register where the mapped operator should be
            placed
            register_length (int): the length of the qubit register

        Returns:
            Four Pauli operators that represent XX, YY, XY and YX at the specified index in the
            current qubit register
        """
        # Define recurrent variables
        prefix_zeros = [0] * register_index
        suffix_zeros = [0] * (register_length - 2 - register_index)
        # Build the Pauli strings
        x_x = Pauli(
            (
                [0] * register_length,
                prefix_zeros + [1, 1] + suffix_zeros,
            )
        )
        x_y = Pauli(
            (
                prefix_zeros + [0, 1] + suffix_zeros,
                prefix_zeros + [1, 1] + suffix_zeros,
            )
        )
        y_x = Pauli(
            (
                prefix_zeros + [1, 0] + suffix_zeros,
                prefix_zeros + [1, 1] + suffix_zeros,
            )
        )
        y_y = Pauli((prefix_zeros + [1, 1] + suffix_zeros, prefix_zeros + [1, 1] + suffix_zeros))
        return x_x, x_y, y_x, y_y