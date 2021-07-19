# -*- coding: utf-8 -*-
import sys
import logging
from builtins import (str, breakpoint)
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
from IPython.lib.pretty import pprint
from numpy import dot as E_dot  # easier to type
from numpy.testing import assert_approx_equal as assert_approx_equal
from scipy.optimize import newton as find_zero_newton

from HARK.ConsumptionSaving.ConsIndShockModelOld \
    import ConsumerSolution as ConsumerSolutionOlder
from HARK.ConsumptionSaving.ConsIndShockModel_Both import (
    define_t_reward,
    def_utility_CRRA, def_value_funcs, def_value_CRRA,
    define_transition
)
from HARK.core import (_log, core_check_condition, MetricObject)
from HARK.distribution import calc_expectation \
    as expect_funcs_across_shocks_given_states
from HARK.interpolation import (CubicInterp, LowerEnvelope, LinearInterp)


class agent_solution(MetricObject):
    """
    Framework for solution of a single stage of a decision problem.

    Provides a foundational structure that all models will build on.  It must
    be specialized and elaborated to solve any particular problem.

    Parameters
    ----------
    soln_futr : agent_solution

    Returns
    -------
    soln_crnt : agent_solution

    Elements of the soln_crnt object contain, but are not limited to:
        Pars : The parameters used in solving the model
        Bilt : Objects constructed and retained from the solution process
        Modl : Equations of the model, in the form of the python code
            that instantiates the computational solution

            At a minimum, this is broken down into:

            States : predetermined variables at the time of decisions
            Controls : variables under control of the decisionmaker
            Reward : current payoff as function of states and controls
            Transitions : evolution of states
            Choices : conditions that determine the agent's choices

            At a minimum, it should result in:

                [dr] : decision rule
                    Maps states into choices
                    Example: consumption function cFunc over market resources
                [v] : value function
                    Bellman value function the agent expects to experience for
                    behaving according to the dynamically optimal plan over
                    the remainder of the horizon.

    stge_kind : dict
        Dictionary with info about this solution stage
        One required entry keeps track of the nature of the stage:
            {'iter_status':'not initialized'}: Before model is set up
            {'iter_status':'finished'}: Stopping requirements are satisfied
                If such requirements are satisfied, {'tolerance':tolerance}
                should exist recording what convergence tolerance was satisfied
            {'iter_status':'iterator'}: Status during iteration
                solution[0].distance_last records the last distance
            {'iter_status':'terminal_partial'}: Bare-bones terminal period/stage
                Does not contain all the info needed to begin solution
                Solver will augment and replace it with 'iterator' stage
        Other uses include keeping track of the nature of the next stage
    completed_cycles : integer
        The number of cycles of the model solved before this call
    solveMethod : str, optional
        The name of the solution method to use, e.g. 'EGM'
    solverType : str, optional
        The name of the type of solver ('HARK', 'Dolo')
    eventTiming : str, optional
        Clarifies timing of any events whose timing might otherwise be ambiguous
    """

    def __init__(self, *args,
                 stge_kind={'iter_status': 'not initialized'},
                 parameters_solver=None,
                 completed_cycles=0,
                 **kwds):
        self.E_Next_ = Nexspectations()  # Next given this period/stage choices
        self.Ante_E_ = Prospectations()  # Before this stage shocks/choices
        self.Pars = Parameters()
        self.Bilt = Built()
        self.Bilt.completed_cycles = completed_cycles
        self.Bilt.stge_kind = stge_kind
        self.Bilt.parameters_solver = parameters_solver
        self.Modl = Modelements()
        self.Modl.Transitions = {}

    def define_transitions_possible(self):
        # Below: Transition types in their canonical possible order

        # These equations should be used by both solution and simulation code

        # We use an OrderedDict so that the Monte Carlo simulation machinery
        # will know the order in which they should be executed
        # (even though the Spyder variable explorer seems, confusingly, to be
        # unable to show an OrderedDict in its intrinsic order)

        # The name of each equation corresponds to a variable that could be
        # preserved in the simulation (or not)

        # Each status will end up transiting only to one subsequent status
        # There are multiple possibilities because models may skip many steps

        # Steps can be skipped when the model is one in which nothing happens
        # in that step, or what happens is so simple that it can be directly
        # captured in a prior transition equation

        # For example, with no end of stage shocks, you could go directly from
        # the "chosen" (after choice) status to the "next_BOP" status

        # Equations that define transitions that affect agent's state
        transitions_possible = {  # BOP: Beginnning of Problem
            'BOP_to_choice': {},
            'choice_to_chosen': {},  # or
            'choice_to_next_BOP': {},
            'chosen_to_EOP': {},  # or
            'chosen_to_next_BOP': {},  # or
            'chosen_to_next_choice': {},
            'EOP_to_next_BOP': {},  # or
            'EOP_to_next_choice': {},  # EOP: End of Problem/Period
        }

        return transitions_possible


class Built(SimpleNamespace):
    """Objects built by solvers during course of solution."""

    pass


class Parameters(SimpleNamespace):
    """Parameters (as passed, and exposed). But not modified."""

    pass


class Expectations(SimpleNamespace):
    """

    Expectations about future period.

    From any point in the current period.
"""

    pass


class Nexspectations(SimpleNamespace):
    """Expectations about future period after current decisions."""

    pass


class Prospectations(SimpleNamespace):
    """Expectations prior to the realization of current period shocks."""

    pass


class ValueFunctions(SimpleNamespace):
    """Expectations across realization of stochastic shocks."""

    pass


class Modelements(SimpleNamespace):
    """Elements of the model in python/HARK code."""

    pass


__all__ = [
    "ConsumerSolutionOlder",
    "ConsumerSolution",
    "ConsumerSolutionOneNrmStateCRRA",
    "ConsPerfForesightSolver",
    "ConsIndShockSetup",
    "ConsIndShockSolverBasic",
    "ConsIndShockSolver",
    "ConsIndShockSetup",
]


# ConsumerSolution does nothing except add agent_solution
# content to original ConsumerSolutionOlder, and set distance_criteria to cFunc

class ConsumerSolution(ConsumerSolutionOlder, agent_solution):
    __doc__ = ConsumerSolutionOlder.__doc__
    __doc__ += """
    In addition, it inherits the attributes of agent_solution.
    """
    # CDC 20210426:
    # vPfunc is unbounded so seems a bad choice for distance; here we change
    # to cFunc but doing so will require recalibrating some of our tests
    #  distance_criteria = ["vPfunc"]  # Bad b/c vP(0)=inf; should use cFunc
    #  distance_criteria = ["vFunc.dm"]  # Bad b/c vP(0)=inf; should use cFunc
    #  distance_criteria = ["mNrmTrg"]  # mNrmTrg better choice if GICNrm holds
    distance_criteria = ["cFunc"]  # cFunc if the GIC fails

    def __init__(self, *args,
                 # TODO: New items below should be in default ConsumerSolution
                 stge_kind={'iter_status': 'not initialized'},
                 completed_cycles=0,
                 parameters_solver=None,
                 **kwds):
        ConsumerSolutionOlder.__init__(self, **kwds)
        agent_solution.__init__(self, *args, **kwds)


class ConsumerSolutionOneNrmStateCRRA(ConsumerSolution):
    """
    ConsumerSolution with CRRA utility and geometric discounting.

    Specializes the generic ConsumerSolution object to the case with:
        * Constant Relative Risk Aversion (CRRA) utility
        * Geometric Discounting of Time Separable Utility
        * Normalizing Growth Factors for "Permanent Income"
          - At the individual and aggregate levels
        * A (potentially nonzero) Probability of Mortality

    and a standard budget constraint involving capital income with a riskfree
    rate of return, and noncapital income that grows by a permanent factor
    (which is used to normalize the problem).

    The model is specified in such a way that it can accommodate either the
    perfect foresight case or the case where noncapital income is subject
    to transitory and permanent shocks, and can accommodate an artificial
    borrowing constraint specified as a proportion of permanent noncapital
    income.

    The class can test for various restrictions on the parameter values of the
    model. A suite of minimal restrictions (like, the time preference factor
    must be nonnegative) is always evaluated. A set of conditions that
    determine infinite horizon charactieristics of the solution can be
    evaluated using the `check_conditions` method.  (Further information about
    the conditions can be found in the documentation for that method.)  For
    convenience, we repeat below the documentation for the parent
    ConsumerSolution of this class, all of which applies here.

    Parameters
    ----------
    DiscFac : float
        Intertemporal discount factor for future utility.
    LivPrb : float
        Survival probability; likelihood of being alive at the beginning of
        the next period.
    CRRA : float
        Coefficient of relative risk aversion.
    Rfree : float
        Risk free interest factor on end-of-period assets.
    PermGroFac : float
        Expected permanent income growth factor at the end of this period.
    """
    __doc__ += ConsumerSolution.__doc__

    def __init__(self, *args,
                 CRRA=2., DiscFac=0.96, LivPrb=1.0, Rfree=1.03, PermGroFac=1.0,
                 **kwds):
        ConsumerSolution.__init__(self, *args, **kwds)

        self.Pars.DiscFac = DiscFac
        self.Pars.LivPrb = LivPrb
        self.Pars.CRRA = CRRA
        self.Pars.Rfree = Rfree
        self.Pars.PermGroFac = PermGroFac

        # These have been moved to Bilt to declutter whiteboard:
        del self.hNrm
        del self.vPfunc
        del self.vPPfunc

        self.Pars.transitions_possible = self.define_transitions_possible()

    def define_transitions_possible(self):
        """
        Construct dictionary containing the possible transition equations.

        Returns
        -------
        possible_transitions : dict
            Names and definitions of the transitions possible within this
        solution object or as an exit from it to the beginning of the next
        object.

        """
        # Working backwards from End of Problem

        # Later we can select among the allowed transitions
        # First, for model with shocks at Beginning of Problem/Period (BOP):
        chosen_to_next_BOP = \
            {'kNrm': 'kNrm = aNrm'}  # k_{t+1} = a_{t}

        choice_to_chosen = \
            {'aNrm': 'aNrm = mNrm - cNrm'}  # a_{t} = m_{t} - c_{t}

        BOP_to_choice = {
            'RNrm': 'RNrm = Rfree / (PermGroFac * permShk)',
            'bNrm': 'bNrm = kNrm * RNrm',
            'yNrm': 'yNrm = tranShk',
            'mNrm': 'mNrm = bNrm + yNrm'}

        # Now, for model with shocks at End of period (EOP)

        chosen_to_next_choice = \
            {'kNrm': 'kNrm = aNrm',
             'RNrm': 'RNrm = Rfree / (PermGroFac * permShk)',
             'bNrm': 'bNrm = kNrm * RNrm',  # b_{t} = k_{t} RNrm_{t}
             'yNrm': 'yNrm = tranShk',  # y_{t} = \tranShk_{t}
             'mNrm': 'mNrm = bNrm + yNrm'}

        # choice_to_chosen need not be redefined b/c same as defined above

        possible_transitions = \
            {'chosen_to_next_BOP': chosen_to_next_BOP,
             'chosen_to_next_choice': chosen_to_next_choice,
             'choice_to_chosen': choice_to_chosen,
             'BOP_to_choice': BOP_to_choice
             }

        return possible_transitions

    def check_conditions(self, messaging_level=0, quietly=False):
        """
        Check whether parameters satisfy some possibly interesting conditions.

        ================================================================
        Acronym        Condition
        ================================================================
        AIC           Absolute Impatience Condition
        RIC           Return Impatience Condition
        GIC           Growth Impatience Condition
        GICLiv        GIC adjusting for constant probability of mortality
        GICNrm        GIC adjusted for uncertainty in permanent income
        FHWC          Finite Human Wealth Condition
        WRIC          Weak Return Impatience Condition
        FVAC          Finite Value of Autarky Condition
        ================================================================

        Depending on the configuration of parameter values, some combination of
        these conditions must be satisfied in order for the problem to have
        a nondegenerate soln_crnt. To check which conditions are required,
        in the verbose mode, a reference to the relevant theoretical literature
        is made.

        Parameters
        ----------
        self : agent_solution

            Solution to the problem described by information for the current
        stage found in Bilt and the succeeding stage.

        messaging_level : int, optional

            Specifies amount of information returned about conditions. When
        zero, returns minimum amount of info; maximum allowed value is 4.

        quietly : boolean, optional

            If true, performs calculations but prints no results

        Returns
        -------
        None
        """
        soln_crnt = self

        Bilt, Pars = soln_crnt.Bilt, soln_crnt.Pars

        Bilt.conditions = {}  # Keep track of truth of conditions
        Bilt.degenerate = False  # True: solution is degenerate

        stdout_handler = logging.StreamHandler(sys.stdout)
        _log.addHandler(stdout_handler)  # "print" it to stdout as well as logfile

        if not quietly:
            msg = '\nFor a model with the following parameter values:\n'
            msg = msg + '\n' + str(Pars.__dict__) + '\n'
            _log.info(msg)
            _log.info(str(Pars.__dict__))
            np.set_printoptions(threshold=20)  # Don't print huge output
            for key in Pars.__dict__.keys():
                _log.info(print('\t' + key + ': ', end=''))
                _log.info(pprint(Pars.__dict__[key]))
            msg = '\nThe following results hold:\n'
            _log.info(msg)

        soln_crnt.check_AIC(soln_crnt, messaging_level)
        soln_crnt.check_FHWC(soln_crnt, messaging_level)
        soln_crnt.check_RIC(soln_crnt, messaging_level)
        soln_crnt.check_GICRaw(soln_crnt, messaging_level)
        soln_crnt.check_GICNrm(soln_crnt, messaging_level)
        soln_crnt.check_GICLiv(soln_crnt, messaging_level)
        soln_crnt.check_FVAC(soln_crnt, messaging_level)

        # degenerate flag is True if the model has no nondegenerate solution
        if hasattr(Bilt, "BoroCnstArt") \
                and Pars.BoroCnstArt is not None:
            Bilt.degenerate = not Bilt.RIC
            # If BoroCnstArt exists but RIC fails, limiting soln is c(m)=0
        else:  # No BoroCnst; not degenerate if neither c(m)=0 or \infty
            Bilt.degenerate = \
                not Bilt.RIC or not Bilt.FHWC

#         soln_crnt = self

#         Bilt, Pars = soln_crnt.Bilt, soln_crnt.Pars

#         Bilt.conditions = {}  # Keep track of truth of conditions
#         Bilt.degenerate = False  # True: solution is degenerate

#         verbose = verbose if verbose is None else verbose

#         if hasattr(self, 'verbose'):  # If not an argument, use default
#             verbose = self.verbose
#         if verbose >= 2:
#             msg = '\nFor a model with the following parameter values:\n'
# #            msg = msg + '\n' + str(Pars.__dict__) + '\n'
#             _log.info(msg)
#             _log.info(str(Pars.__dict__))
#             # np.set_printoptions(threshold=20)  # Don't print huge output
#             # for key in Pars.__dict__.keys():
#             #     print('\t' + key + ': ', end='')
#             #     pprint(Pars.__dict__[key])
#             msg = '\nThe following results hold:\n'
#             _log.info(msg)

#         soln_crnt.check_AIC(soln_crnt, verbose)
#         soln_crnt.check_FHWC(soln_crnt, verbose)
#         soln_crnt.check_RIC(soln_crnt, verbose)
#         soln_crnt.check_GICRaw(soln_crnt, verbose)
#         soln_crnt.check_GICNrm(soln_crnt, verbose)
#         soln_crnt.check_GICLiv(soln_crnt, verbose)
#         soln_crnt.check_FVAC(soln_crnt, verbose)

    # # Passing the soln_crnt object to check_conditions might seem unnecessary,
    # # since its first argument is self, and during the solution process the
    # # solver has soln_crnt attached to its self, so its contents could be
    # # obtained from self.soln_crnt.  But after the solution is
    # # completed, the solution that is returned should be a standalone object
    # # that does not reside on a solver instance. Doing things as below allows
    # # such a standalone a solution object to check not only its own conditions
    # # but even to check the conditions of another solution.
    # def check_conditions_old(self, soln_crnt, verbose=None):
    #     """
    #     Check whether the instance's type satisfies a set of conditions.

    #     ================================================================
    #     Acronym        Condition
    #     ================================================================
    #     AIC           Absolute Impatience Condition
    #     RIC           Return Impatience Condition
    #     GIC           Growth Impatience Condition
    #     GICLiv        GIC adjusting for constant probability of mortality
    #     GICNrm        GIC adjusted for uncertainty in permanent income
    #     FHWC          Finite Human Wealth Condition
    #     FVAC          Finite Value of Autarky Condition
    #     ================================================================

    #     Depending on the configuration of parameter values, some combination of
    #     these conditions must be satisfied in order for the problem to have
    #     a nondegenerate soln_crnt. To check which conditions are required,
    #     in the verbose mode, a reference to the relevant theoretical literature
    #     is made.

    #     Parameters
    #     ----------
    #     verbose : int

    #     Specifies different levels of verbosity of feedback. When False, it
    #     only reports whether the instance's type fails to satisfy a particular
    #     condition. When True, it reports all results, i.e.  the factor values
    #     for all conditions.

    #     soln_crnt : ConsumerSolution
    #     Solution to the problem described by information
    #     for the current stage found in Bilt and the succeeding stage.

    #     Returns
    #     -------
    #     None
    #     """

    #     Bilt, Pars = soln_crnt.Bilt, soln_crnt.Pars

    #     Bilt.conditions = {}  # Keep track of truth of conditions
    #     Bilt.degenerate = False  # True: solution is degenerate

    #     verbose = verbose if verbose is None else verbose

    #     if hasattr(self, 'verbose'):  # If not an argument, use default
    #         verbose = self.verbose

    #     if verbose >= 2:
    #         msg = '\nFor a model with the following parameter values:\n'
    #         msg = msg + '\n' + str(Pars.__dict__) + '\n'
    #         _log.info(msg)
    #         _log.info(str(Pars.__dict__))
    #         np.set_printoptions(threshold=20)  # Don't print huge output
    #         for key in Pars.__dict__.keys():
    #             print('\t' + key + ': ', end='')
    #             pprint(Pars.__dict__[key])
    #         msg = '\nThe following results hold:\n'
    #         _log.info(msg)

    #     soln_crnt.check_AIC(soln_crnt, verbose)
    #     soln_crnt.check_FHWC(soln_crnt, verbose)
    #     soln_crnt.check_RIC(soln_crnt, verbose)
    #     soln_crnt.check_GICRaw(soln_crnt, verbose)
    #     soln_crnt.check_GICNrm(soln_crnt, verbose)
    #     soln_crnt.check_GICLiv(soln_crnt, verbose)
    #     soln_crnt.check_FVAC(soln_crnt, verbose)

    #     # degenerate flag is True if the model has no nondegenerate solution
    #     if hasattr(Bilt, "BoroCnstArt") \
    #             and Pars.BoroCnstArt is not None:
    #         Bilt.degenerate = not Bilt.RIC
    #         # If BoroCnstArt exists but RIC fails, limiting soln is c(m)=0
    #     else:  # No BoroCnst; not degenerate if neither c(m)=0 or \infty
    #         Bilt.degenerate = \
    #             not Bilt.RIC or not Bilt.FHWC

    def check_AIC(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on the Absolute Impatience Condition."""
        name = "AIC"

        def test(stge): return stge.Bilt.APF < 1

        messages = {
            True: "\n\nThe Absolute Patience Factor for the supplied parameter values, APF={0.APF}, satisfies the Absolute Impatience Condition (AIC), which requires APF < 1:\n    " +
                  stge.Bilt.AIC_fcts['urlhandle'],
            False: "\n\nThe Absolute Patience Factor for the supplied parameter values, APF={0.APF}, violates the Absolute Impatience Condition (AIC), which requires APF < 1:\n    " +
                   stge.Bilt.AIC_fcts['urlhandle']
        }
        verbose_messages = {
            True: "\n  Because the APF < 1,  the absolute amount of consumption is expected to fall over time.  \n",
            False: "\n  Because the APF > 1, the absolute amount of consumption is expected to grow over time.  \n",
        }

        stge.Bilt.AIC = core_check_condition(name, test, messages, messaging_level,
                                             verbose_messages, "APF", stge)

    def check_FVAC(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on the Finite Value of Autarky Condition."""
        name = "FVAC"

        def test(stge): return stge.Bilt.FVAF < 1

        messages = {
            True: "\n\nThe Finite Value of Autarky Factor for the supplied parameter values, FVAF={0.FVAF}, satisfies the Finite Value of Autarky Condition, which requires FVAF < 1:\n    " +
                  stge.Bilt.FVAC_fcts['urlhandle'],
            False: "\n\nThe Finite Value of Autarky Factor for the supplied parameter values, FVAF={0.FVAF}, violates the Finite Value of Autarky Condition, which requires FVAF:\n    " +
                   stge.Bilt.FVAC_fcts['urlhandle']
        }
        verbose_messages = {
            True: "\n  Therefore, a nondegenerate solution exists if the RIC also holds. (" + stge.Bilt.FVAC_fcts[
                'urlhandle'] + ")\n",
            False: "\n  Therefore, a nondegenerate solution exits if the RIC holds, but will not exist if the RIC fails unless the FHWC also fails.\n",
        }

        stge.Bilt.FVAC = core_check_condition(name, test, messages, messaging_level,
                                              verbose_messages, "FVAF", stge)

    def check_GICRaw(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on the Growth Impatience Condition."""
        name = "GICRaw"

        def test(stge): return stge.Bilt.GPFRaw < 1

        messages = {
            True: "\n\nThe Growth Patience Factor for the supplied parameter values, GPF={0.GPFRaw}, satisfies the Growth Impatience Condition (GIC), which requires GPF < 1:\n    " +
                  stge.Bilt.GICRaw_fcts['urlhandle'],
            False: "\n\nThe Growth Patience Factor for the supplied parameter values, GPF={0.GPFRaw}, violates the Growth Impatience Condition (GIC), which requires GPF < 1:\n    " +
                   stge.Bilt.GICRaw_fcts['urlhandle'],
        }
        verbose_messages = {
            True: "\n  Therefore,  for a perfect foresight consumer, the ratio of individual wealth to permanent income is expected to fall indefinitely.    \n",
            False: "\n  Therefore, for a perfect foresight consumer whose parameters satisfy the FHWC, the ratio of individual wealth to permanent income is expected to rise toward infinity. \n"
        }
        stge.Bilt.GICRaw = core_check_condition(name, test, messages, messaging_level,
                                                verbose_messages, "GPFRaw", stge)

    def check_GICLiv(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on Mortality Adjusted GIC."""
        name = "GICLiv"

        def test(stge): return stge.Bilt.GPFLiv < 1

        messages = {
            True: "\n\nThe Mortality Adjusted Aggregate Growth Patience Factor for the supplied parameter values, GPFLiv={0.GPFLiv}, satisfies the Mortality Adjusted Aggregate Growth Impatience Condition (GICLiv):\n    " +
                  stge.Bilt.GPFLiv_fcts['urlhandle'],
            False: "\n\nThe Mortality Adjusted Aggregate Growth Patience Factor for the supplied parameter values, GPFLiv={0.GPFLiv}, violates the Mortality Adjusted Aggregate Growth Impatience Condition (GICLiv):\n    " +
                   stge.Bilt.GPFLiv_fcts['urlhandle'],
        }
        verbose_messages = {
            True: "\n  Therefore, a target level of the ratio of aggregate market resources to aggregate permanent income exists (" +
                  stge.Bilt.GPFLiv_fcts['urlhandle'] + ")\n",
            False: "\n  Therefore, a target ratio of aggregate resources to aggregate permanent income may not exist (" +
                   stge.Bilt.GPFLiv_fcts['urlhandle'] + ")\n",
        }
        stge.Bilt.GICLiv = core_check_condition(name, test, messages, messaging_level,
                                                verbose_messages, "GPFLiv", stge)

    def check_RIC(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on the Return Impatience Condition."""
        name = "RIC"

        def test(stge): return stge.Bilt.RPF < 1

        messages = {
            True: "\n\nThe Return Patience Factor for the supplied parameter values, RPF= {0.RPF}, satisfies the Return Impatience Condition (RIC), which requires RPF < 1:\n    " +
                  stge.Bilt.RPF_fcts['urlhandle'],
            False: "\n\nThe Return Patience Factor for the supplied parameter values, RPF= {0.RPF}, violates the Return Impatience Condition (RIC), which requires RPF < 1:\n    " +
                   stge.Bilt.RPF_fcts['urlhandle'],
        }
        verbose_messages = {
            True: "\n  Therefore, the limiting consumption function is not c(m)=0 for all m\n",
            False: "\n  Therefore, if the FHWC is satisfied, the limiting consumption function is c(m)=0 for all m.\n",
        }
        stge.Bilt.RIC = core_check_condition(name, test, messages, messaging_level,
                                             verbose_messages, "RPF", stge)

    def check_FHWC(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on the Finite Human Wealth Condition."""
        name = "FHWC"

        def test(stge): return stge.Bilt.FHWF < 1

        messages = {
            True: "\n\nThe Finite Human Wealth Factor for the supplied parameter values, FHWF={0.FHWF}, satisfies the Finite Human Wealth Condition (FHWC), which requires FHWF < 1:\n    " +
                  stge.Bilt.FHWC_fcts['urlhandle'],
            False: "\n\nThe Finite Human Wealth Factor for the supplied parameter values, FHWF={0.FHWF}, violates the Finite Human Wealth Condition (FHWC), which requires FHWF < 1:\n    " +
                   stge.Bilt.FHWC_fcts['urlhandle'],
        }
        verbose_messages = {
            True: "\n  Therefore, the limiting consumption function is not c(m)=Infinity.\n  Human wealth normalized by permanent income is {0.hNrmInf}.\n",
            False: "\n  Therefore, the limiting consumption function is c(m)=Infinity for all m unless the RIC is also violated.\n  If both FHWC and RIC fail and the consumer faces a liquidity constraint, the limiting consumption function is nondegenerate but has a limiting slope of 0. (" +
                   stge.Bilt.FHWC_fcts['urlhandle'] + ")\n",
        }
        stge.Bilt.FHWC = core_check_condition(name, test, messages, messaging_level,
                                              verbose_messages, "FHWF", stge)

    def check_GICNrm(self, stge, messaging_level=0, quietly=False):
        """Check Normalized Growth Patience Factor."""
        if not hasattr(stge.Pars, 'IncShkDstn'):
            return  # GICNrm is same as GIC for PF consumer

        name = "GICNrm"

        def test(stge): return stge.Bilt.GPFNrm <= 1

        messages = {
            True: "\n\nThe Normalized Growth Patience Factor GPFNrm for the supplied parameter values, GPFNrm={0.GPFNrm}, satisfies the Normalized Growth Impatience Condition (GICNrm), which requires GPFNrm < 1:\n    " +
                  stge.Bilt.GICNrm_fcts['urlhandle'],
            False: "\n\nThe Normalized Growth Patience Factor GPFNrm for the supplied parameter values, GPFNrm={0.GPFNrm}, violates the Normalized Growth Impatience Condition (GICNrm), which requires GPFNrm < 1:\n    " +
                   stge.Bilt.GICNrm_fcts['urlhandle'],
        }
        verbose_messages = {
            True: "\n  Therefore, a target level of the individual market resources ratio m exists.",
            False: "\n  Therefore, a target ratio of individual market resources to individual permanent income does not exist.  (" +
                   stge.Bilt.GICNrm_fcts['urlhandle'] + ")\n",
        }

        stge.Bilt.GICNrm = core_check_condition(name, test, messages, messaging_level,
                                                verbose_messages, "GPFNrm", stge)

    def check_WRIC(self, stge, messaging_level=0, quietly=False):
        """Evaluate and report on the Weak Return Impatience Condition."""
        if not hasattr(stge, 'IncShkDstn'):
            return  # WRIC is same as RIC for PF consumer

        name = "WRIC"

        def test(stge): return stge.Bilt.WRPF <= 1

        messages = {
            True: "\n\nThe Weak Return Patience Factor for the supplied parameter values, WRPF={0.WRPF}, satisfies the Weak Return Impatience Condition, which requires WRPF < 1:\n    " +
                  stge.Bilt.WRIC_fcts['urlhandle'],
            False: "\n\nThe Weak Return Patience Factor for the supplied parameter values, WRPF={0.WRPF}, violates the Weak Return Impatience Condition, which requires WRPF < 1:\n    " +
                   stge.Bilt.WRIC_fcts['urlhandle'],
        }

        verbose_messages = {
            True: "\n  Therefore, a nondegenerate solution exists if the FVAC is also satisfied. (" +
                  stge.Bilt.WRIC_fcts['urlhandle'] + ")\n",
            False: "\n  Therefore, a nondegenerate solution is not available (" + stge.Bilt.WRIC_fcts[
                'urlhandle'] + ")\n",
        }
        stge.Bilt.WRIC = core_check_condition(
            name, test, messages, messaging_level, verbose_messages, "WRPF", stge)

    def mNrmTrg_find(self):
        """
        Find value of m at which individual consumer expects m not to change.

        This will exist if the GICNrm holds.

        https://econ-ark.github.io/BufferStockTheory#UniqueStablePoints

        Returns
        -------
            The target value mNrmTrg.
        """
        m_init_guess = self.Bilt.mNrmMin + self.E_Next_.IncNrmNxt
        try:  # Find value where argument is zero
            self.Bilt.mNrmTrg = find_zero_newton(
                self.E_Next_.m_tp1_minus_m_t,
                m_init_guess)
        except:
            self.Bilt.mNrmTrg = None

        return self.Bilt.mNrmTrg

    def mNrmStE_find(self):
        """
        Find pseudo Steady-State Equilibrium (normalized) market resources m.

        This is the m at which the consumer
        expects level of market resources M to grow at same rate as the level
        of permanent income P.

        This will exist if the GIC holds.

        https://econ-ark.github.io/BufferStockTheory#UniqueStablePoints

        Parameters
        ----------
        self : ConsumerSolution
            Solution to this period's problem, which must have attribute cFunc.

        Returns
        -------
        self : ConsumerSolution
            Same solution that was passed, but now with attribute mNrmStE.
        """
        # Minimum market resources plus E[next income] is okay starting guess

        m_init_guess = self.Bilt.mNrmMin + self.E_Next_.IncNrmNxt
        try:
            self.Bilt.mNrmStE = find_zero_newton(
                self.E_Next_.permShk_tp1_times_m_tp1_minus_m_t, m_init_guess)
        except:
            self.Bilt.mNrmStE = None

        # Add mNrmStE to the solution and return it
        return self.Bilt.mNrmStE


# Until this point, our objects have been "solution" not "solver" objects.  To
# a "solution" object, "solver" objects add the tools that can generate a
# solution of that kind.  Eventually we aim to have a clear delineation between
# models objects, solution objects, and solvers, so that a model can be
# specified independent of a solution method, a solution can be specified
# independent of a model or solution method, and a solution method can be
# specified that can solve many different models.

# As a first step in that direction, solver classes do not __init__ themselves
# with the __init__ method of their "parent" solution class.  Instead, they
# expect to receive as an argument an instance of a solution object called
# solution_next, and they will construct a soln_crnt object that begins
# empty and onto which the information describing the model and its solution
# are added step by step.


class ConsPerfForesightSolver(ConsumerSolutionOneNrmStateCRRA):
    """
    Solve (one period of) perfect foresight CRRA utility consumer problem.

    Augments ConsumerSolutionOneNrmStateCRRA_PF solution object with methods
    able to solve a perfect foresight model with those characteristics,
    allowing for a bequest motive and either a natural or an artificial
    borrowing constraint.

    Parameters
    ----------
    solution_next : ConsumerSolution
        The solution to next period's one-period problem.
    DiscFac : float
        Intertemporal discount factor for future utility.
    LivPrb : float
        Survival probability; likelihood of being alive at the beginning of
        the next period.
    CRRA : float
        Coefficient of relative risk aversion.
    Rfree : float
        Risk free interest factor on end-of-period assets.
    PermGroFac : float
        Expected permanent income growth factor at the end of this period.
    BoroCnstArt : float or None
        Artificial borrowing constraint, as a multiple of permanent income.
        Can be None, indicating no artificial constraint.
    MaxKinks : int, optional
        Maximum number of kink points to allow in the consumption function;
        additional points will be thrown out.  Only relevant in infinite
        horizon model with artificial borrowing constraint.
    """

    # CDC 20200426: MaxKinks adds a lot of complexity to no evident purpose
    # because everything it accomplishes could be done solving a finite horizon
    # model (including tests of convergence conditions, which can be invoked
    # manually if a user wants them).
    def __init__(
            self, solution_next,  # Successor solution is mandatory first arg
            DiscFac=0.96, LivPrb=1.0, CRRA=2.0, Rfree=1.0,
            PermGroFac=1.0, BoroCnstArt=None, MaxKinks=None,
            # Solver has extra parameters that solution does not
            solverType='HARK',
            solveMethod='EGM',
            eventTiming='EOP',
            horizon='infinite',
            **kwds):

        soln_futr = self.soln_futr = solution_next

        # Do NOT __init__ as a ConsumerSolutionOneNrmStateCRRA object
        # even though that is the parent class
        # Because this is a SOLVER (which, admittedly, present, can only
        # handle that class). But in principle, this is a solver, not a
        # solution

        # In principle, a solution need not know its solver
        soln_crnt = self.soln_crnt = \
            ConsumerSolutionOneNrmStateCRRA(
                self, DiscFac, LivPrb, CRRA, Rfree, PermGroFac, eventTiming)

        # Get solver parameters and store for later use
        # omitting things that are not needed

        Pars = soln_crnt.Pars
        Pars.__dict__.update(
            {k: v for k, v in {**kwds, **locals()}.items()
             if k not in {'self', 'solution_next', 'kwds', 'soln_futr',
                          'soln_crnt', 'Pars'}})

        # 'terminal' solution should replace pseudo_terminal:
        if hasattr(soln_futr.Bilt, 'stge_kind') and \
                soln_futr.Bilt.stge_kind['iter_status'] == 'terminal_partial':
            soln_crnt.Bilt = deepcopy(soln_futr.Bilt)

        # links for docs; urls are used when "fcts" are added
        self._url_doc_for_solver_get()

        return

    def _url_doc_for_solver_get(self):
        # Generate a url that will locate the documentation
        self.class_name = self.__class__.__name__
        self.soln_crnt.Bilt.url_ref = self.url_ref = \
            "https://econ-ark.github.io/BufferStockTheory"
        self.soln_crnt.Bilt.urlroot = self.urlroot = \
            self.url_ref + '/#'
        self.soln_crnt.Bilt.url_doc = self.url_doc = \
            "https://hark.readthedocs.io/en/latest/search.html?q=" + \
            self.class_name + "&check_keywords=yes&area=default#"

    def from_chosen_states_make_continuation_E_Next_(self, crnt):
        """
        Construct expectations of useful objects from post-choice status.

        Parameters
        ----------
        crnt : agent_solution
            The solution to the problem without the expectations info.

        Returns
        -------
        crnt : agent_solution
            The given solution, with the relevant namespaces updated to
        contain the constructed info.
        """
        crnt = self.build_facts_infhor()
        crnt = self.build_facts_recursive()

        # Reduce cluttered formulae with local aliases
        E_Next_, tp1 = crnt.E_Next_, self.soln_futr
        Bilt, Pars = crnt.Bilt, crnt.Pars
        Rfree, PermGroFac, DiscLiv = Pars.Rfree, Pars.PermGroFac, Pars.DiscLiv

        CRRA = tp1.vFunc.CRRA

        # Omit first and last pts; they define extrapo below and above kinks
        Bilt.mNrm_kinks_tp1 = mNrm_kinks_tp1 = tp1.cFunc.x_list[:-1][1:]
        Bilt.cNrm_kinks_tp1 = cNrm_kinks_tp1 = tp1.cFunc.y_list[:-1][1:]
        Bilt.vNrm_kinks_tp1 = vNrm_kinks_tp1 = tp1.vFunc(mNrm_kinks_tp1)

        # Calculate end-of-this-period aNrm vals that would reach those mNrm's
        # There are no shocks in the PF model, so tranShkMin = tranShk = 1.0
        bNrm_kinks_tp1 = (mNrm_kinks_tp1 - tp1.Pars.tranShkMin)
        aNrm_kinks = bNrm_kinks_tp1 * (PermGroFac / Rfree)

        crnt.Bilt.aNrmGrid = aNrm_kinks

        # Level and first derivative of expected value from aNrmGrid points
        v_0 = DiscLiv * \
            PermGroFac ** (1 - CRRA) * vNrm_kinks_tp1
        v_1 = DiscLiv * \
            PermGroFac ** (0 - CRRA) * tp1.Bilt.u.dc(cNrm_kinks_tp1) * Rfree

        c_0 = cNrm_kinks_tp1

        E_Next_.given_shocks = np.array([v_0, v_1, c_0])

        # Make positions of precomputed objects easy to reference
        E_Next_.v0_pos, E_Next_.v1_pos = 0, 1
        E_Next_.c0_pos = 3

        return crnt

    def make_cFunc_PF(self):
        """
        Make (piecewise linear) consumption function for this period.

        See PerfForesightConsumerType.ipynb notebook for derivations.
        """
        # Reduce cluttered formulae with local aliases
        crnt, tp1 = self.soln_crnt, self.soln_futr
        Bilt, Pars, E_Next_ = crnt.Bilt, crnt.Pars, crnt.E_Next_
        Rfree, PermGroFac, MPCmin = Pars.Rfree, Pars.PermGroFac, Bilt.MPCmin

        BoroCnstArt, DiscLiv, BoroCnstNat = \
            Pars.BoroCnstArt, Pars.DiscLiv, Bilt.BoroCnstNat

        u, u.Nvrs, u.dc.Nvrs = Bilt.u, Bilt.u.Nvrs, Bilt.u.dc.Nvrs
        CRRA_tp1 = tp1.Bilt.vFunc.CRRA

        # define yNrm_tp1 to make formulas below easier to read
        yNrm_tp1 = tp1.Pars.tranShkMin  # in PF model tranShk[Min,Max] = 1.0

        if BoroCnstArt is None:
            BoroCnstArt = -np.inf

        # Whichever constraint is tighter is the relevant one
        BoroCnst = max(BoroCnstArt, BoroCnstNat)

        # Translate t+1 constraints into their consequences for t
        # Endogenous Gridpoints steps:
        # c today yielding u' equal to discounted u' from each kink in t+1
        cNrm_kinks = crnt.Bilt.u.dc.Nvrs(E_Next_.given_shocks[E_Next_.v1_pos])
        mNrm_kinks = Bilt.aNrmGrid + cNrm_kinks  # Corresponding m

        # Corresponding value and inverse value
        vNrm_kinks = E_Next_.given_shocks[E_Next_.v0_pos]
        vInv_kinks = u.Nvrs(vNrm_kinks)

        # vAdd used later to add some new points; should add zero to existing
        vAdd_kinks = mNrm_kinks - mNrm_kinks  # makes zero array of useful size

        mNrmMin_tp1 = yNrm_tp1 + BoroCnst * (Rfree / PermGroFac)

        # by t_E_ here we mean "discounted back to period t"
        t_E_v_tp1_at_mNrmMin_tp1 = \
            (DiscLiv * PermGroFac ** (1 - CRRA_tp1) * tp1.vFunc(mNrmMin_tp1))

        t_E_v1_tp1_at_mNrmMin_tp1 = \
            (((Rfree * DiscLiv) * (PermGroFac ** (-CRRA_tp1))
              ) * tp1.vFunc.dm(mNrmMin_tp1))

        # h is the 'horizon': h_t(m_t) is the number of periods it will take
        # before you hit the constraint, after which you remain constrained

        # The maximum h in a finite horizon model
        # is the remaining number of periods of life: h = T - t

        # If the consumer is sufficiently impatient, there will be levels of
        # m from which the optimal plan will be to run down existing wealth
        # over some horizon less than T - t, and for the remainder of the
        # horizon to set consumption equal to income

        # In a given period t, it will be optimal to spend all resources
        # whenever the marginal utility of doing so exceeds the marginal
        # utility yielded by receiving the minimum possible income next
        # period: u'(m_t) > (discounted) u'(c(y_{t+1}))

        # "cusp" is name for where current period constraint stops binding
        cNrm_cusp = u.dc.Nvrs(t_E_v1_tp1_at_mNrmMin_tp1)
        vNrm_cusp = Bilt.u(cNrm_cusp) + t_E_v_tp1_at_mNrmMin_tp1
        vAdd_cusp = t_E_v_tp1_at_mNrmMin_tp1
        vInv_cusp = u.Nvrs(vNrm_cusp)
        mNrm_cusp = cNrm_cusp + BoroCnst

        # cusp today vs today's implications of future constraints
        if mNrm_cusp >= mNrm_kinks[-1]:  # tighter than the tightest existing
            mNrm_kinks = np.array([mNrm_cusp])  # looser kinka are irrelevant
            cNrm_kinks = np.array([cNrm_cusp])  # forget about them all
            vNrm_kinks = np.array([vNrm_cusp])
            vInv_kinks = np.array([vInv_cusp])
            vAdd_kinks = np.array([vAdd_cusp])
        else:  # keep today's implications of future kinks that matter today
            first_reachable = np.where(mNrm_kinks >= mNrm_cusp)[0][-1]
            if first_reachable < mNrm_kinks.size - 1:  # Keep binding pts
                mNrm_kinks = mNrm_kinks[first_reachable:-1]
                cNrm_kinks = cNrm_kinks[first_reachable:-1]
                vInv_kinks = vInv_kinks[first_reachable:-1]
                vAdd_kinks = vAdd_kinks[first_reachable:-1]
            # Add the new kink introduced by today's constraint
            mNrm_kinks = np.insert(mNrm_kinks, 0, mNrm_cusp)
            cNrm_kinks = np.insert(cNrm_kinks, 0, cNrm_cusp)
            vNrm_kinks = np.insert(vNrm_kinks, 0, vNrm_cusp)

        #        vAddGrid = np.append(vAdd_cusp, vAdd_kinks)
        #        vAddGrid = np.append(vAddGrid, 0.)
        #        breakpoint()

        # Add a point to construct cFunc, vFunc as PF solution beyond last kink
        mNrmGrid_unconst = np.append(mNrm_kinks, mNrm_kinks[-1] + 1)  # m + 1
        cNrmGrid_unconst = np.append(cNrm_kinks, cNrm_kinks[-1] + 1*MPCmin)
        #        aNrmGrid_unconst = mNrmGrid_unconst-cNrmGrid_unconst  # a_t=m_t-c_t
        # DBC : m_{t+1} = (Rfree/PermGroFac)a_{t} + y_{t+1}
        #        mNrmGrid_tp1_unconst = aNrmGrid_unconst*(Rfree/PermGroFac)+yNrm_tp1
        # v_{t} = u_{t} + [dicounted] v_{t+1}
        #        vNrmGrid_unconst = u(cNrmGrid_unconst) + \
        #            (DiscLiv * PermGroFac**(1-CRRA_tp1) *
        #             tp1.vFunc(mNrmGrid_tp1_unconst))
        #        # u Invert it
        #        vInvGrid_unconst = u.Nvrs(vNrmGrid_unconst)
        # No reason to construct dv/dm: It's going to be available via
        # u'(cFunc) in any case, and the correct representation of its
        # proper inverse at non-kink points is as a Heaviside function which
        # is a pain  to interpolate
        #        vInvPGrid_unconst = \
        #            (((1-CRRA)*vNrmGrid_unconst)**(-1+1/(1-CRRA))) * \
        #            (cNrmGrid_unconst**(-CRRA))
        #        vInvPGrid_unconst_v2 = u.dc.Nvrs()
        #       c_from_vInvPGrid_unconst = \
        #            ((vInvPGrid_unconst/(((1-CRRA)*vNrmGrid_unconst) **
        #                                 (-1+1/(1-CRRA)))))**(-1/CRRA)

        #        mNrmGrid_const = np.array([BoroCnst, mNrm_cusp, mNrm_cusp+1])
        #        mNrmGrid_const = np.array([BoroCnst, mNrm_cusp])
        #        uNrmGrid_const = np.array([float('inf'), u(mNrm_cusp), float('inf')])
        #        uNrmGrid_const = np.array([float('inf'), u(mNrm_cusp)])
        #        uInvGrid_const = u.Nvrs(uNrmGrid_const)

        # # Add point(s) at infinity with value of zero
        # def vAddFunc(m, mNrmGrid, vAddGrid):
        #     mNrmGridPlus = np.append(mNrmGrid, float('inf'))
        #     vAddGridPlus = np.append(vAddGrid, vAddGrid[-1])
        #     from collections import Iterable
        #     if isinstance(m, Iterable):
        #         from itertools import repeat
        #         points_at_infinity = \
        #             np.array(
        #                 list(
        #                     map(lambda m, mNrmGridPlus, vAddGridPlus:
        #                      vAddGridPlus[np.where(m < mNrmGridPlus)[0][0]],
        #                         m, repeat(mNrmGridPlus), repeat(vAddGridPlus)
        #                         )))
        #         return points_at_infinity
        #     else:
        #         return vAddGridPlus[np.where(m < mNrmGridPlus)[0][0]]

        #         vInvFunc_unconst = \
        #             LinearInterp(mNrmGrid_unconst, vInvGrid_unconst)

        # #        from HARK.utilities import plot_funcs
        # #        plot_funcs(lambda x: np.heaviside(x-BoroCnst,0.5),1,2)
        #         uInvFunc_const = \
        #             LinearInterp(mNrmGrid_const, uInvGrid_const)
        #         vFunc_const = Bilt.u(uInvGrid_const)+t_E_v_tp1_at_mNrmMin_tp1
        #         vFunc_unconst = Bilt.u(vInvGrid_unconst)

        #         def vAddFunc(m, mGrid, vAddGrid):
        #             return vAddGrid[np.where(m < mGrid)[0][0]]

        #        breakpoint()
        #        vNrmGrid_const=[BoroCnst,u(mNrmGrid_unconst[0])]

        mNrmGrid = np.append([BoroCnst], mNrmGrid_unconst)
        cNrmGrid = np.append(0., cNrmGrid_unconst)
        #        vInvGrid = np.append(0., vInvGrid_unconst)
        #        vInvPGrid = np.append(float('inf'), vInvPGrid_unconst)
        #        vInvGrid = np.insert(vInvGrid, 0, -1)

        # Above last kink point, use PF solution
        #        mNrmGrid = np.append(mNrmGrid, mNrmGrid[-1]+1)
        #        cNrmGrid = np.append(cNrmGrid, cNrmGrid[-1]+MPCmin)
        #        aNrmGrid = mNrmGrid - cNrmGrid
        #        bNrmGrid_tp1 = aNrmGrid*(Rfree/PermGroFac)
        #        mNrmGrid_tp1 = bNrmGrid_tp1+folw.PF_IncNrm_tp1
        #        vNrmGrid = u(cNrmGrid)+(DiscLiv * PermGroFac**(1-CRRA_tp1) *
        #             folw.vFunc_tp1(mNrmGrid_tp1))
        #        vInvGrid = (vNrmGrid*(1-CRRA))**(1/(1-CRRA))

        #     vInvGrid = np.append(vInvGrid, vInvGrid[-1]+MPCmin**(-CRRA/(1.0-CRRA)))

        # To guarantee meeting BoroCnst, if mNrm = BoroCnst then cNrm = 0.
        #        mNrmGrid = np.append([BoroCnst], mNrm_kinks)
        #        cNrmGrid = np.append(0., cNrm_kinks)

        # Above last kink point, use PF solution
        #        mNrmGrid = np.append(mNrmGrid, mNrmGrid[-1]+1)
        #        cNrmGrid = np.append(cNrmGrid, cNrmGrid[-1]+MPCmin)

        self.cFunc = self.soln_crnt.cFunc = Bilt.cFunc = \
            LinearInterp(mNrmGrid, cNrmGrid)

    #        vInvFunc_unconst = self.vFuncNvrs = \
    #            LinearInterp(mNrmGrid,vInvGrid)

    # self.vNvrsFunc.derivative(m)
    # (vNvrsFunc.derivative(/(((1-CRRA)*vInvGrid_unconst)**(-1+1/(1-CRRA))))

    #        cc = self.cFunc_from_vFunc(2.0)
    #        cFromvP=uPinv(vP_Grid)
    #        cFuncFromvNvrsFunc = LinearInterp(mNrmGrid,cFromvP)
    #        from HARK.utilities import plot_funcs
    #        plot_funcs([self.cFunc,cFuncFromvNvrsFunc],0,2)

    #        print('hi')
    #        PF_t_v_tp1_last = (DiscLiv*(PermGroFac ** (1-CRRA_tp1)))*\
    #            np.float(folw.vFunc_tp1((Rfree/PermGroFac)*aNrmGrid[-1]+E_Next_.IncNrmNxt))
    #        PF_t_vNvrs_tp1_Grid_2 = \
    #            np.append(PF_t_vNvrs_tp1_Grid,PF_t_v_tp1_last)
    # vNvrsGrid = Bilt.uinv(Bilt.u(cNrmGrid)+ folw.u_tp1(PF_t_vNvrs_tp1_Grid))

    # If the mNrm that would unconstrainedly yield next period's bottom pt
    #        if BoroCnst > mNrmGrid_pts[0]: # is prohibited by BoroCnst
    #            satisfies_BoroCnst = np.where(
    #                mNrmGrid_unconst - BoroCnst < cNrm_from_aNrmMin) # True if OK

    # Amount of m in excess of minimum possible m
    #        mNrmXtraGrid = mNrmGrid_pts - BoroCnst

    #         # Add the point corresponding to
    #    mNrmGrid = np.unique(np.insert(mNrmGrid,0,E_Next_.IncNrmNxt-BoroCnstArt))
    #     cNrmGrid = np.unique(np.insert(cNrmGrid,0,E_Next_.IncNrmNxt-BoroCnstArt))

    # #        vNvrs_tp1 = (DiscLiv * LivPrb) * folw.vFunc_tp1(mNrmGrid_tp1)
    # #        PF_t_vNvrs_tp1_Grid = folw.uinv_tp1(DiscLiv) * \
    # #            folw.vFuncNvrs_tp1.y_list
    #         # Beginning-of-period-tp1 marginal value vec is vP_tp1
    # #        vP_tp1 = folw.uP_tp1(cNrmGrid_tp1)
    #         # Corresponding end-of-period-t marginal value is _vP_t
    # #        _vP_t = ((DiscLiv * Rfree) * (PermGroFac**(-CRRA_tp1)))*vP_tp1
    # #        _vP_t =
    #         # Endogenous gridpoints method
    #  #       cNrmGrid = Bilt.uPinv(_vP_t)    # EGM step 1: u' inverse yields c
    #         mNrmGrid = aNrmGrid + cNrmGrid  # EGM step 2: DBC inverted

    #     cNrmGrid = np.unique(np.insert(cNrmGrid,0,E_Next_.IncNrmNxt-BoroCnstArt))

    #         # Add additional point to the list of gridpoints for extrapolation,
    #         # using this period's new value of the lower bound of the MPC, which
    #         # defines the PF unconstrained problem through the end of the horizon
    # #        mNrmGrid_interp_pts = np.append(mNrmGrid, mNrmGrid[-1] + 1.0)
    # #        cNrmGrid_interp_pts = np.append(cNrmGrid, cNrmGrid[-1] + MPCmin)
    #         # If artificial borrowing constraint binds, combine constrained and
    #         # unconstrained consumption functions.

    #         # The problem is well-defined down to BoroCnstArt even if in
    #         # principle from t you could not get to any m_tp1 < E_Next_.IncNrmNxt
    #     # because nothing prevents you from starting tp1 with m \geq BoroCnstArt
    #  #       if BoroCnstArt < mNrmGrid[0] - E_Next_.IncNrmNxt:
    #  #       mNrmGrid_interp_pts = np.append([BoroCnstArt], mNrmGrid_interp_pts)
    #  #      cNrmGrid_interp_pts = np.append([BoroCnstArt], cNrmGrid_interp_pts)
    # #        else: # BoroCnstArt is irrelevant if BoroCnstNat is tighter
    #         # cNrmGridCnst defines points where cnst would bind for each m gpt
    # #        cNrmGridCnst = mNrmGrid_interp_pts - BoroCnstArt
    #         mXtraGrid = mNrmGrid - BoroCnstArt # m in excess of minimum m
    # #        idx_binding =   # c > possible
    #         # highest m where cnst binds
    #         idx_binding_last = np.where(mNrmXtraGrid <= cNrmGrid)[0][-1]
    #         if idx_binding_last < (mNrmGrid.size - 1):
    #             print('hi')
    #             # # If not the *very last* index, find the the critical level
    #             # # of mNrmGrid where artificial cnst begins to bind.
    #             # d0 = cNrmGrid[idx] - cNrmGridCnst[idx]
    #             # d1 = cNrmGridCnst[idx + 1] - cNrmGrid[idx + 1]
    #             # m0 = mNrmGrid[idx]
    #             # m1 = mNrmGrid[idx + 1]
    #             # alpha = d0 / (d0 + d1)
    #             # mCrit = m0 + alpha * (m1 - m0)
    #         # # Adjust grids of mNrmGrid and cNrmGrid to account for constraint.
    #             # cCrit = mCrit - BoroCnstArt
    #     # mNrmGrid = np.concatenate(([BoroCnstArt, mCrit], mNrmGrid[(idx + 1):]))
    #             # cNrmGrid = np.concatenate(([0.0, cCrit], cNrmGrid[(idx + 1):]))
    #             # aNrmGrid = mNrmGrid-cNrmGrid
    #         else:
    #             # If it *is* the last index, then there are only three points
    #             # that characterize the c function: the artificial borrowing
    #             # constraint, the constraint kink, and the extrapolation point
    #             mAbve= 1.0 # (cNrmGrid[-1] - cNrmGridCnst[-1]) / (1.0 - MPCmin)
    #             mNrm_max_bindpoint = mNrmGrid[idx_binding_last]
    #             cNrm_max_bindpoint = cNrmGrid[idx_binding_last]
    # #                mCrit = mNrmGrid[-1] + mXtra
    # #                cCrit = mCrit - BoroCnstArt
    #             mNrmGrid_Xtra = np.array(
    #                 [BoroCnstArt, mNrm_max_bindpoint, mNrm_max_bindpoint + 1])
    #             cNrmGrid_Xtra = np.array(
    #                 [0.0, cNrm_max_bindpoint, cNrm_max_bindpoint + MPCmin])
    #             aNrmGrid_Xtra = mNrmGrid_Xtra- cNrmGrid_Xtra
    #         # If mNrmGrid, cNrmGrid grids have become too large, throw out last
    #             # kink point, being sure to adjust the extrapolation.

    #         if mNrmGrid.size > MaxKinks:
    #             mNrmGrid = np.concatenate((mNrmGrid[:-2], [cNrmGrid[-3] + 1.0]))
    #         cNrmGrid = np.concatenate((cNrmGrid[:-2], [cNrmGrid[-3] + MPCmin]))
    #             aNrmGrid = mNrmGrid - cNrmGrid

    # Consumption function is a linear interpolation between kink pts
    #        self.cFunc = self.soln_crnt.cFunc = Bilt.cFunc = \
    #            LinearInterp(mNrmGrid_pts, cNrmGrid_pts)

    #        PF_t_v_tp1_last = (DiscLiv*(PermGroFac ** (1-CRRA_tp1)))*\
    #            np.float(folw.vFunc_tp1((Rfree/PermGroFac)*aNrmGrid[-1]+E_Next_.IncNrmNxt))
    #        PF_t_vNvrs_tp1_Grid_2 = \
    #            np.append(PF_t_vNvrs_tp1_Grid,PF_t_v_tp1_last)

    # vNvrsGrid = Bilt.uinv(Bilt.u(cNrmGrid)+ folw.u_tp1(PF_t_vNvrs_tp1_Grid))

    # # Calculate the upper bound of the MPC as the slope of bottom segment
    # # In practice, this is always 1.  Code is here for clarity
    # Bilt.MPCmax = ((cNrmGrid_Xtra[1] - cNrmGrid_Xtra[0])/
    #                (mNrmGrid_Xtra[1] - mNrmGrid_Xtra[0]))

    # # Lower bound of mNrm is lowest gridpoint -- usually 0
    # Bilt.mNrmMin = mNrmGrid_Xtra[0]

    # # Add the calculated grids to self.Bilt
    # Bilt.aNrmGrid = aNrmGrid_Xtra
    # Bilt._vP_t = _vP_t
    # Bilt.cNrmGrid = cNrmGrid_Xtra
    # Bilt.mNrmGrid = mNrmGrid_Xtra

    # Add approximation to v and vP
    #        breakpoint()
    #        Bilt.vNvrs = self.soln_crnt.uinv(_vP_t)

    def build_facts_infhor(self):
        """
        Calculate facts useful for characterizing infinite horizon models.

        Parameters
        ----------
        solution: ConsumerSolution
            Solution that already has minimal requirements (vPfunc, cFunc)

        Returns
        -------
        solution : ConsumerSolution
            The given solution, with the relevant namespaces updated to
        contain the constructed info.
        """
        soln_crnt = self.soln_crnt  # current
        Bilt, Pars, E_Next_ = soln_crnt.Bilt, soln_crnt.Pars, soln_crnt.E_Next_

        urlroot = Bilt.urlroot
        Pars.DiscLiv = Pars.DiscFac * Pars.LivPrb
        # givens are not changed by the calculations below; Bilt and E_Next_ are
        givens = {**Pars.__dict__}

        APF_fcts = {
            'about': 'Absolute Patience Factor'
        }
        py___code = '((Rfree * DiscLiv) ** (1.0 / CRRA))'
        Bilt.APF = APF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        APF_fcts.update({'latexexpr': r'\APF'})
        APF_fcts.update({'_unicode_': r'Þ'})
        APF_fcts.update({'urlhandle': urlroot + 'APF'})
        APF_fcts.update({'py___code': py___code})
        APF_fcts.update({'value_now': APF})
        Bilt.APF_fcts = APF_fcts

        AIC_fcts = {
            'about': 'Absolute Impatience Condition'
        }
        AIC_fcts.update({'latexexpr': r'\AIC'})
        AIC_fcts.update({'urlhandle': urlroot + 'AIC'})
        AIC_fcts.update({'py___code': 'test: APF < 1'})
        Bilt.AIC_fcts = AIC_fcts

        RPF_fcts = {
            'about': 'Return Patience Factor'
        }
        py___code = 'APF / Rfree'
        Bilt.RPF = RPF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        RPF_fcts.update({'latexexpr': r'\RPF'})
        RPF_fcts.update({'_unicode_': r'Þ_R'})
        RPF_fcts.update({'urlhandle': urlroot + 'RPF'})
        RPF_fcts.update({'py___code': py___code})
        RPF_fcts.update({'value_now': RPF})
        Bilt.RPF_fcts = RPF_fcts

        RIC_fcts = {
            'about': 'Growth Impatience Condition'
        }
        RIC_fcts.update({'latexexpr': r'\RIC'})
        RIC_fcts.update({'urlhandle': urlroot + 'RIC'})
        RIC_fcts.update({'py___code': 'test: RPF < 1'})
        Bilt.RIC_fcts = RIC_fcts

        GPFRaw_fcts = {
            'about': 'Growth Patience Factor'
        }
        py___code = 'APF / PermGroFac'
        Bilt.GPFRaw = GPFRaw = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        GPFRaw_fcts.update({'latexexpr': r'\GPFRaw'})
        GPFRaw_fcts.update({'_unicode_': r'Þ_Γ'})
        GPFRaw_fcts.update({'urlhandle': urlroot + 'GPFRaw'})
        GPFRaw_fcts.update({'py___code': py___code})
        GPFRaw_fcts.update({'value_now': GPFRaw})
        Bilt.GPFRaw_fcts = GPFRaw_fcts

        GICRaw_fcts = {
            'about': 'Growth Impatience Condition'
        }
        GICRaw_fcts.update({'latexexpr': r'\GICRaw'})
        GICRaw_fcts.update({'urlhandle': urlroot + 'GICRaw'})
        GICRaw_fcts.update({'py___code': 'test: GPFRaw < 1'})
        Bilt.GICRaw_fcts = GICRaw_fcts

        GPFLiv_fcts = {
            'about': 'Mortality-Adjusted Growth Patience Factor'
        }
        py___code = 'APF * LivPrb / PermGroFac'
        Bilt.GPFLiv = GPFLiv = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        GPFLiv_fcts.update({'latexexpr': r'\GPFLiv'})
        GPFLiv_fcts.update({'urlhandle': urlroot + 'GPFLiv'})
        GPFLiv_fcts.update({'py___code': py___code})
        GPFLiv_fcts.update({'value_now': GPFLiv})
        Bilt.GPFLiv_fcts = GPFLiv_fcts

        GICLiv_fcts = {
            'about': 'Growth Impatience Condition'
        }
        GICLiv_fcts.update({'latexexpr': r'\GICLiv'})
        GICLiv_fcts.update({'urlhandle': urlroot + 'GICLiv'})
        GICLiv_fcts.update({'py___code': 'test: GPFLiv < 1'})
        Bilt.GICLiv_fcts = GICLiv_fcts

        RNrm_PF_fcts = {
            'about': 'Growth-Normalized PF Return Factor'
        }
        py___code = 'Rfree/PermGroFac'
        E_Next_.RNrm_PF = RNrm_PF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        RNrm_PF_fcts.update({'latexexpr': r'\PFRNrm'})
        RNrm_PF_fcts.update({'_unicode_': r'R/Γ'})
        RNrm_PF_fcts.update({'py___code': py___code})
        RNrm_PF_fcts.update({'value_now': RNrm_PF})
        E_Next_.RNrm_PF_fcts = RNrm_PF_fcts

        Inv_RNrm_PF_fcts = {
            'about': 'Inv of Growth-Normalized PF Return Factor'
        }
        py___code = '1 / RNrm_PF'
        E_Next_.Inv_RNrm_PF = Inv_RNrm_PF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        Inv_RNrm_PF_fcts.update({'latexexpr': r'\InvPFRNrm'})
        Inv_RNrm_PF_fcts.update({'_unicode_': r'Γ/R'})
        Inv_RNrm_PF_fcts.update({'py___code': py___code})
        Inv_RNrm_PF_fcts.update({'value_now': Inv_RNrm_PF})
        E_Next_.Inv_RNrm_PF_fcts = \
            Inv_RNrm_PF_fcts

        FHWF_fcts = {
            'about': 'Finite Human Wealth Factor'
        }
        py___code = 'PermGroFac / Rfree'
        Bilt.FHWF = FHWF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        FHWF_fcts.update({'latexexpr': r'\FHWF'})
        FHWF_fcts.update({'_unicode_': r'R/Γ'})
        FHWF_fcts.update({'urlhandle': urlroot + 'FHWF'})
        FHWF_fcts.update({'py___code': py___code})
        FHWF_fcts.update({'value_now': FHWF})
        Bilt.FHWF_fcts = \
            FHWF_fcts

        FHWC_fcts = {
            'about': 'Finite Human Wealth Condition'
        }
        FHWC_fcts.update({'latexexpr': r'\FHWC'})
        FHWC_fcts.update({'urlhandle': urlroot + 'FHWC'})
        FHWC_fcts.update({'py___code': 'test: FHWF < 1'})
        Bilt.FHWC_fcts = FHWC_fcts

        hNrmInf_fcts = {
            'about': 'Human wealth for inf hor'
        }
        py___code = '1/(1-FHWF) if (FHWF < 1) else float("inf")'
        Bilt.hNrmInf = hNrmInf = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        hNrmInf_fcts = dict({'latexexpr': r'1/(1-\FHWF)'})
        hNrmInf_fcts.update({'value_now': hNrmInf})
        hNrmInf_fcts.update({'py___code': py___code})
        Bilt.hNrmInf_fcts = hNrmInf_fcts

        DiscGPFRawCusp_fcts = {
            'about': 'DiscFac s.t. GPFRaw = 1'
        }
        py___code = '( PermGroFac                       **CRRA)/(Rfree)'
        Bilt.DiscGPFRawCusp = DiscGPFRawCusp = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        DiscGPFRawCusp_fcts.update({'latexexpr':
                                    r'\PermGroFac^{\CRRA}/\Rfree'})
        DiscGPFRawCusp_fcts.update({'value_now': DiscGPFRawCusp})
        DiscGPFRawCusp_fcts.update({'py___code': py___code})
        Bilt.DiscGPFRawCusp_fcts = \
            DiscGPFRawCusp_fcts

        DiscGPFLivCusp_fcts = {
            'about': 'DiscFac s.t. GPFLiv = 1'
        }
        py___code = '( PermGroFac                       **CRRA)/(Rfree*LivPrb)'
        Bilt.DiscGPFLivCusp = DiscGPFLivCusp = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        DiscGPFLivCusp_fcts.update({'latexexpr':
                                    r'\PermGroFac^{\CRRA}/(\Rfree\LivPrb)'})
        DiscGPFLivCusp_fcts.update({'value_now': DiscGPFLivCusp})
        DiscGPFLivCusp_fcts.update({'py___code': py___code})
        Bilt.DiscGPFLivCusp_fcts = DiscGPFLivCusp_fcts

        FVAF_fcts = {  # overwritten by version with uncertainty
            'about': 'Finite Value of Autarky Factor'
        }
        py___code = 'LivPrb * DiscLiv'
        Bilt.FVAF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        FVAF_fcts.update({'latexexpr': r'\FVAFPF'})
        FVAF_fcts.update({'urlhandle': urlroot + 'FVAFPF'})
        FVAF_fcts.update({'py___code': py___code})
        Bilt.FVAF_fcts = FVAF_fcts

        FVAC_fcts = {  # overwritten by version with uncertainty
            'about': 'Finite Value of Autarky Condition - Perfect Foresight'
        }
        FVAC_fcts.update({'latexexpr': r'\FVACPF'})
        FVAC_fcts.update({'urlhandle': urlroot + 'FVACPF'})
        FVAC_fcts.update({'py___code': 'test: FVAFPF < 1'})
        Bilt.FVAC_fcts = FVAC_fcts

        E_Next_.IncNrmNxt_fcts = {  # Overwritten by version with uncertainty
            'about': 'Expected income next period'
        }
        py___code = '1.0'
        E_Next_.IncNrmNxt = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        #        E_Next_.IncNrmNxt_fcts.update({'latexexpr': r'ExIncNrmNxt'})
        #        E_Next_.IncNrmNxt_fcts.update({'_unicode_': r'R/Γ'})
        #        E_Next_.IncNrmNxt_fcts.update({'urlhandle': urlroot+'ExIncNrmNxt'})
        E_Next_.IncNrmNxt_fcts.update({'py___code': py___code})
        E_Next_.IncNrmNxt_fcts.update({'value_now': E_Next_.IncNrmNxt})
        soln_crnt.E_Next_.IncNrmNxt_fcts = E_Next_.IncNrmNxt_fcts

        RNrm_PF_fcts = {
            'about': 'Expected Growth-Normalized Return'
        }
        py___code = 'Rfree / PermGroFac'
        E_Next_.RNrm_PF = RNrm_PF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        RNrm_PF_fcts.update({'latexexpr': r'\PFRNrm'})
        RNrm_PF_fcts.update({'_unicode_': r'R/Γ'})
        RNrm_PF_fcts.update({'urlhandle': urlroot + 'PFRNrm'})
        RNrm_PF_fcts.update({'py___code': py___code})
        RNrm_PF_fcts.update({'value_now': RNrm_PF})
        E_Next_.RNrm_PF_fcts = RNrm_PF_fcts

        RNrm_PF_fcts = {
            'about': 'Expected Growth-Normalized Return'
        }
        py___code = 'Rfree / PermGroFac'
        E_Next_.RNrm_PF = RNrm_PF = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        RNrm_PF_fcts.update({'latexexpr': r'\PFRNrm'})
        RNrm_PF_fcts.update({'_unicode_': r'R/Γ'})
        RNrm_PF_fcts.update({'urlhandle': urlroot + 'PFRNrm'})
        RNrm_PF_fcts.update({'py___code': py___code})
        RNrm_PF_fcts.update({'value_now': RNrm_PF})
        E_Next_.RNrm_PF_fcts = RNrm_PF_fcts

        DiscLiv_fcts = {
            'about': 'Mortality-Inclusive Discounting'
        }
        py___code = 'DiscFac * LivPrb'
        Bilt.DiscLiv = DiscLiv = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        DiscLiv_fcts.update({'latexexpr': r'\PFRNrm'})
        DiscLiv_fcts.update({'_unicode_': r'R/Γ'})
        DiscLiv_fcts.update({'urlhandle': urlroot + 'PFRNrm'})
        DiscLiv_fcts.update({'py___code': py___code})
        DiscLiv_fcts.update({'value_now': DiscLiv})
        Bilt.DiscLiv_fcts = DiscLiv_fcts

    def build_facts_recursive(self):
        """
        For t, calculate results that depend on the last period solved (t+1).

        Returns
        -------
        None.

        """
        soln_crnt = self.soln_crnt
        tp1 = self.soln_futr.Bilt  # tp1 means t+1
        Bilt, Pars, E_Next_ = soln_crnt.Bilt, soln_crnt.Pars, soln_crnt.E_Next_

        givens = {**Pars.__dict__, **locals()}
        urlroot = Bilt.urlroot
        Pars.DiscLiv = Pars.DiscFac * Pars.LivPrb

        hNrm_fcts = {
            'about': 'Human Wealth '
        }
        py___code = '((PermGroFac / Rfree) * (1.0 + tp1.hNrm))'
        if soln_crnt.stge_kind['iter_status'] == 'terminal_partial':  # kludge:
            py___code = '0.0'  # hNrm = 0.0 for last period
        Bilt.hNrm = hNrm = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        hNrm_fcts.update({'latexexpr': r'\hNrm'})
        hNrm_fcts.update({'_unicode_': r'R/Γ'})
        hNrm_fcts.update({'urlhandle': urlroot + 'hNrm'})
        hNrm_fcts.update({'py___code': py___code})
        hNrm_fcts.update({'value_now': hNrm})
        Bilt.hNrm_fcts = hNrm_fcts

        BoroCnstNat_fcts = {
            'about': 'Natural Borrowing Constraint'
        }
        py___code = '(tp1.mNrmMin - tranShkMin)*(PermGroFac/Rfree)*permShkMin'
        if soln_crnt.stge_kind['iter_status'] == 'terminal_partial':  # kludge
            py___code = 'hNrm'  # Presumably zero
        Bilt.BoroCnstNat = BoroCnstNat = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        BoroCnstNat_fcts.update({'latexexpr': r'\BoroCnstNat'})
        BoroCnstNat_fcts.update({'_unicode_': r''})
        BoroCnstNat_fcts.update({'urlhandle': urlroot + 'BoroCnstNat'})
        BoroCnstNat_fcts.update({'py___code': py___code})
        BoroCnstNat_fcts.update({'value_now': BoroCnstNat})
        Bilt.BoroCnstNat_fcts = BoroCnstNat_fcts

        BoroCnst_fcts = {
            'about': 'Effective Borrowing Constraint'
        }
        py___code = 'BoroCnstNat if (BoroCnstArt == None) else ' + \
            '(BoroCnstArt if BoroCnstNat < BoroCnstArt else BoroCnstNat)'
        Bilt.BoroCnst = BoroCnst = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        BoroCnst_fcts.update({'latexexpr': r'\BoroCnst'})
        BoroCnst_fcts.update({'_unicode_': r''})
        BoroCnst_fcts.update({'urlhandle': urlroot + 'BoroCnst'})
        BoroCnst_fcts.update({'py___code': py___code})
        BoroCnst_fcts.update({'value_now': BoroCnst})
        Bilt.BoroCnst_fcts = BoroCnst_fcts

        # MPCmax is not a meaningful object in the PF model so is not created
        # there so create it here
        MPCmax_fcts = {
            'about': 'Maximal MPC in current period as m -> mNrmMin'
        }
        py___code = '1.0 / (1.0 + (RPF / tp1.MPCmax))'
        if soln_crnt.stge_kind['iter_status'] == 'terminal_partial':  # kludge:
            soln_crnt.tp1.MPCmax = float('inf')  # => MPCmax = 1 for last per
        Bilt.MPCmax = eval(
            py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        MPCmax_fcts.update({'latexexpr': r''})
        MPCmax_fcts.update({'urlhandle': urlroot + 'MPCmax'})
        MPCmax_fcts.update({'py___code': py___code})
        MPCmax_fcts.update({'value_now': Bilt.MPCmax})
        Bilt.MPCmax_fcts = MPCmax_fcts

        mNrmMin_fcts = {
            'about': 'Min m is the max you can borrow'
        }
        py___code = 'BoroCnst'
        Bilt.mNrmMin = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        mNrmMin_fcts.update({'latexexpr': r'\mNrmMin'})
        mNrmMin_fcts.update({'py___code': py___code})
        Bilt.mNrmMin_fcts = mNrmMin_fcts

        MPCmin_fcts = {
            'about': 'Minimal MPC in current period as m -> infty'
        }
        py___code = '1.0 / (1.0 + (RPF / tp1.MPCmin))'
        if soln_crnt.stge_kind['iter_status'] == 'terminal_partial':  # kludge:
            py__code = '1.0'
        Bilt.MPCmin = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        MPCmin_fcts.update({'latexexpr': r''})
        MPCmin_fcts.update({'urlhandle': urlroot + 'MPCmin'})
        MPCmin_fcts.update({'py___code': py___code})
        MPCmin_fcts.update({'value_now': Bilt.MPCmin})
        Bilt.MPCmin_fcts = MPCmin_fcts

        MPCmax_fcts = {
            'about': 'Maximal MPC in current period as m -> mNrmMin'
        }
        py___code = '1.0 / (1.0 + (RPF / tp1.MPCmax))'
        if soln_crnt.stge_kind['iter_status'] == 'terminal_partial':  # kludge:
            Bilt.tp1.MPCmax = float('inf')  # => MPCmax = 1 for final period
        Bilt.MPCmax = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        MPCmax_fcts.update({'latexexpr': r''})
        MPCmax_fcts.update({'urlhandle': urlroot + 'MPCmax'})
        MPCmax_fcts.update({'py___code': py___code})
        MPCmax_fcts.update({'value_now': Bilt.MPCmax})
        Bilt.MPCmax_fcts = MPCmax_fcts

        cFuncLimitIntercept_fcts = {
            'about':
                'Vertical intercept of perfect foresight consumption function'}
        py___code = 'MPCmin * hNrm'
        Bilt.cFuncLimitIntercept = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        cFuncLimitIntercept_fcts.update({'py___code': py___code})
        cFuncLimitIntercept_fcts.update({'latexexpr': r'\MPC \hNrm'})
        soln_crnt.Bilt.cFuncLimitIntercept_fcts = cFuncLimitIntercept_fcts

        cFuncLimitSlope_fcts = {
            'about': 'Slope of limiting consumption function'}
        py___code = 'MPCmin'
        cFuncLimitSlope_fcts.update({'py___code': 'MPCmin'})
        Bilt.cFuncLimitSlope = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        cFuncLimitSlope_fcts.update({'py___code': py___code})
        cFuncLimitSlope_fcts = dict({'latexexpr': r'\MPCmin'})
        cFuncLimitSlope_fcts.update({'urlhandle': r'\MPC'})
        soln_crnt.Bilt.cFuncLimitSlope_fcts = cFuncLimitSlope_fcts

        # That's the end of things that are identical for PF and non-PF models
        # Models with uncertainty will supplement the above calculations

        return soln_crnt

    def def_value(self):
        """
        Build value function and store results in Modl.value.

        Returns
        -------
        soln : solution object with value functions attached

        """
        return def_value_CRRA(self.soln_crnt, self.soln_crnt.Pars.CRRA)

    def solve_prepared_stage_divert(self):
        """
        Allow alternative solution method in special cases.

        Returns
        -------
        divert : boolean
            If False (usually), continue normal solution
            If True, produce alternative solution and store on self.soln_crnt
        """
        # bare-bones default terminal solution does not have all the facts
        # we need, because it is generic (for any u func) so add the facts
        crnt, futr = self.soln_crnt, self.soln_futr
        if futr.Bilt.stge_kind['iter_status'] != 'terminal_partial':
            return False  # Continue with normal solution procedures
        else:
            crnt = define_t_reward(crnt, def_utility_CRRA)  # Bellman reward

            crnt.cFunc = crnt.Bilt.cFunc  # make cFunc accessible
            crnt = self.def_value()  # make value functions using cFunc
            crnt.vFunc = crnt.Bilt.vFunc  # make vFunc accessible for distance
            self.build_facts_infhor()
            crnt.Bilt.stge_kind['iter_status'] = 'iterator'  # now it's legit
            return True  # if pseudo_terminal=True, enhanced replaces original

    def solve_prepared_stage(self):  # inside ConsPerfForesightSolver
        """
        Solve one stage/period of the consumption-saving problem.

        Parameters
        ----------
        None (all are already in self)

        Returns
        -------
        solution : ConsumerSolution
            The solution to this period/stage's problem
        """
        if self.solve_prepared_stage_divert():  # Allow bypass of normal soln
            return self.soln_crnt  # created by solve_prepared_stage_divert

        crnt = self.soln_crnt

        define_transition(crnt, 'chosen_to_next_choice')

        self.from_chosen_states_make_continuation_E_Next_(crnt)

        define_t_reward(crnt, def_utility_CRRA)  # Bellman reward: utility

        self.make_t_decision_rules_and_value_functions(crnt)

        return crnt

    # alias for core.py which calls .solve method
    solve = solve_prepared_stage

    def make_t_decision_rules_and_value_functions(self, crnt):
        """
        Add decision rules and value funcs to current solution object.

        Parameters
        ----------
        crnt : agent_solution

        Returns
        -------
        agent_solution : agent_solution
            Augmented with decision rules and value functions

        """
        self.make_cFunc_PF()
        return def_value_funcs(crnt, crnt.Pars.CRRA)

    def solver_prep_solution_for_an_iteration(self):  # PF
        """Prepare current stage for processing by the one-stage solver."""
        soln_crnt = self.soln_crnt

        Bilt, Pars = soln_crnt.Bilt, soln_crnt.Pars

        # Catch degenerate case of zero-variance income distributions
        # Otherwise "test cases" that try the degenerate dstns will fail
        if hasattr(Pars, "tranShkVals") and hasattr(Pars, "permShkVals"):
            if ((Pars.tranShkMin == 1.0) and (Pars.permShkMin == 1.0)):
                soln_crnt.E_Next_.Inv_permShk = 1.0
                soln_crnt.E_Next_.uInv_permShk = 1.0
        else:  # Missing trans or permShkVals; assume it's PF model
            Pars.tranShkMin = Pars.permShkMin = 1.0

        # Nothing needs to be done for terminal_partial
        if hasattr(Bilt, 'stge_kind'):
            if 'iter_status' in Bilt.stge_kind:
                if (Bilt.stge_kind['iter_status'] == 'terminal_partial'):
                    # solution_terminal is handmade, do not remake
                    return

        Bilt.stge_kind = \
            soln_crnt.stge_kind = {'iter_status': 'iterator',
                                   'slvr_type': self.__class__.__name__}

        return

    # Disambiguate "prepare_to_solve" from similar method names elsewhere
    # (preserve "prepare_to_solve" as alias because core.py calls it)
    prepare_to_solve = solver_prep_solution_for_an_iteration


##############################################################################

class ConsIndShockSetup(ConsPerfForesightSolver):
    """
    Solve one period of CRRA problem with transitory and permanent shocks.

    Superclass for solvers of one period consumption-saving problems with
    constant relative risk aversion utility and permanent and transitory shocks
    to labor income, containing code shared among alternative specific solvers.

    N.B.: Because this is a one stge solver, objects that (in the full problem)
    are lists because they are allowed to vary at different stages, are scalars
    here because the value that is appropriate for the current stage is the one
    that will be passed.

    Parameters
    ----------
    solution_next : ConsumerSolution
        The solution to next period's one period problem.
    IncShkDstn : distribution.Distribution
        A discrete approximation to the income process between the period
        being solved and the one immediately following
    LivPrb : float
        Survival probability; likelihood of being alive at the beginning of
        the succeeding period.
    DiscFac : float
        Intertemporal discount factor for future utility.
    CRRA : float
        Coefficient of relative risk aversion.
    Rfree : float
        Risk free interest factor on end-of-period assets.
    PermGroFac : float
        Expected permanent income growth factor at the end of this period.
    BoroCnstArt: float or None
        Borrowing constraint for the minimum allowable assets to end the
        period with.  If it is less than the natural borrowing constraint,
        then it is irrelevant; BoroCnstArt=None indicates no artificial bor-
        rowing constraint.
    aXtraGrid: np.array
        Array of "extra" end-of-period asset values-- assets above the
        absolute minimum acceptable level.
    CubicBool: boolean
        An indicator for whether the solver should use cubic or linear inter-
        polation.
    solveMethod : str, optional
        Solution method to use
    eventTiming : str, optional
        Stochastic shocks or deterministic evolutions of the problem (aside
        from state variable transitions) can occur between choice stages. The
        informaton about these events needs to be attached to the appropriate
        solution stage, and executed at the appropriate point. This option allows
        changing the interpretation of an existing variable, e.g. income shocks,
        between the allowed timings.
             'EOP' is 'End of problem/period'
             'BOP' is 'Beginning of problem/period'
             'Both' there are things to do both before and after the decision stage
    """

    shock_vars = ['tranShkDstn', 'permShkDstn']  # Unemp shock=min(transShkVal)

    # TODO: CDC 20210416: Params shared with PF are in different order. Fix
    def __init__(
            self, solution_next, IncShkDstn, LivPrb, DiscFac, CRRA, Rfree,
            PermGroFac, BoroCnstArt, aXtraGrid, vFuncBool, CubicBool,
            permShkDstn, tranShkDstn,
            solveMethod='EGM',
            eventTiming='EOP',
            horizon='infinite',
            **kwds):
        # First execute PF solver init
        # We must reorder params by hand in case someone tries positional solve
        ConsPerfForesightSolver.__init__(self, solution_next, DiscFac=DiscFac,
                                         LivPrb=LivPrb, CRRA=CRRA,
                                         Rfree=Rfree, PermGroFac=PermGroFac,
                                         BoroCnstArt=BoroCnstArt,
                                         IncShkDstn=IncShkDstn,
                                         permShkDstn=permShkDstn,
                                         tranShkDstn=tranShkDstn,
                                         solveMethod=solveMethod,
                                         eventTiming=eventTiming,
                                         horizon=horizon,
                                         **kwds)

        # ConsPerfForesightSolver.__init__ makes self.soln_crnt
        soln_crnt = self.soln_crnt

        # Things we have built, exogenous parameters, and model structures:
        Bilt, Pars, Modl = soln_crnt.Bilt, soln_crnt.Pars, soln_crnt.Modl

        Modl.eventTiming = eventTiming
        Modl.horizon = horizon

        Bilt.aXtraGrid = aXtraGrid
        self.CubicBool = CubicBool

        # In which column is each object stored in IncShkDstn?
        Pars.permPos = IncShkDstn.parameters['ShkPosn']['perm']
        Pars.tranPos = IncShkDstn.parameters['ShkPosn']['tran']

        # Bcst are "broadcasted" values: serial list of every permutation
        # Makes it fast to take expectations using E_dot
        Pars.permShkValsBcst = permShkValsBcst = IncShkDstn.X[Pars.permPos]
        Pars.tranShkValsBcst = tranShkValsBcst = IncShkDstn.X[Pars.tranPos]

        Pars.ShkPrbs = ShkPrbs = IncShkDstn.pmf

        Pars.permShkPrbs = permShkPrbs = permShkDstn.pmf
        Pars.permShkVals = permShkVals = permShkDstn.X
        # Confirm that perm shocks have expectation near one
        assert_approx_equal(E_dot(permShkPrbs, permShkVals), 1.0)

        Pars.tranShkPrbs = tranShkPrbs = tranShkDstn.pmf
        Pars.tranShkVals = tranShkVals = tranShkDstn.X
        # Confirm that tran shocks have expectation near one
        assert_approx_equal(E_dot(tranShkPrbs, tranShkVals), 1.0)

        Pars.permShkMin = permShkMin = np.min(permShkVals)
        Pars.tranShkMin = tranShkMin = np.min(tranShkVals)

        Pars.permShkMax = permShkMax = np.max(permShkVals)
        Pars.tranShkMax = tranShkMax = np.max(tranShkVals)

        Pars.UnempPrb = Pars.tranShkPrbs[0]

        Pars.inc_min_Prb = np.sum(  # All cases where perm and tran Shk are Min
            ShkPrbs[ \
                permShkValsBcst * tranShkValsBcst == permShkMin * tranShkMin
            ]
        )

        Pars.inc_max_Prb = np.sum(  # All cases where perm and tran Shk are Min
            ShkPrbs[ \
                permShkValsBcst * tranShkValsBcst == permShkMax * tranShkMax
            ]
        )
        Pars.inc_max_Val = permShkMax * tranShkMax

    def build_facts_infhor(self):
        """
        Calculate expectations and facts for models with uncertainty.

        For versions with uncertainty in transitory and/or permanent shocks,
        adds to the solution a set of results useful for calculating
        various diagnostic conditions about the problem, and stable
        points (if they exist).

        Parameters
        ----------
        solution: ConsumerSolution
            Solution to this period's problem, which must have attribute cFunc.

        Returns
        -------
        solution : ConsumerSolution
            Same solution that was provided, augmented with the factors

        """
        super().build_facts_infhor()  # Make the facts built by the PF model

        soln_crnt = self.soln_crnt

        Bilt, Pars, E_Next_ = soln_crnt.Bilt, soln_crnt.Pars, soln_crnt.E_Next_

        # The 'givens' do not change as facts are constructed
        givens = {**Pars.__dict__, **soln_crnt.__dict__}

        Bilt.E_dot = E_dot  # add dot product expectations operator to envt

        urlroot = Bilt.urlroot

        # Many other _fcts will have been inherited from the perfect foresight

        # Here we need compute only those objects whose value changes from PF
        # (or does not exist in PF case)

        E_Next_.IncNrmNxt_fcts = {
            'about': 'Expected income next period'
        }
        py___code = 'E_dot(ShkPrbs, tranShkValsBcst * permShkValsBcst)'
        E_Next_.IncNrmNxt = E_Next_.IncNrmNxt = eval(
            py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        E_Next_.IncNrmNxt_fcts.update({'latexexpr': r'ExIncNrmNxt'})
        E_Next_.IncNrmNxt_fcts.update({'_unicode_': r'E[tranShk permShk]= 1.0'})
        E_Next_.IncNrmNxt_fcts.update({'urlhandle': urlroot + 'ExIncNrmNxt'})
        E_Next_.IncNrmNxt_fcts.update({'py___code': py___code})
        E_Next_.IncNrmNxt_fcts.update({'value_now': E_Next_.IncNrmNxt})
        soln_crnt.E_Next_.IncNrmNxt_fcts = E_Next_.IncNrmNxt_fcts

        E_Next_.Inv_permShk_fcts = {
            'about': 'Expected Inverse of Permanent Shock'
        }
        py___code = 'E_dot(1/permShkVals, permShkPrbs)'
        E_Next_.Inv_permShk = E_Next_.Inv_permShk = eval(
            py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        E_Next_.Inv_permShk_fcts.update({'latexexpr': r'\ExInvpermShk'})
        E_Next_.Inv_permShk_fcts.update({'urlhandle': urlroot + 'ExInvpermShk'})
        E_Next_.Inv_permShk_fcts.update({'py___code': py___code})
        E_Next_.Inv_permShk_fcts.update({'value_now': E_Next_.Inv_permShk})
        soln_crnt.E_Next_.Inv_permShk_fcts = E_Next_.Inv_permShk_fcts

        E_Next_.RNrm_fcts = {
            'about': 'Expected Stochastic-Growth-Normalized Return'
        }
        py___code = 'RNrm_PF * E_Next_.Inv_permShk'
        E_Next_.RNrm = eval(
            py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        E_Next_.RNrm_fcts.update({'latexexpr': r'\ExRNrm'})
        E_Next_.RNrm_fcts.update({'_unicode_': r'E[R/Γψ]'})
        E_Next_.RNrm_fcts.update({'urlhandle': urlroot + 'ExRNrm'})
        E_Next_.RNrm_fcts.update({'py___code': py___code})
        E_Next_.RNrm_fcts.update({'value_now': E_Next_.RNrm})
        E_Next_.RNrm_fcts = E_Next_.RNrm_fcts

        E_Next_.uInv_permShk_fcts = {
            'about': 'Expected Utility for Consuming Permanent Shock'
        }
        py___code = 'E_dot(permShkValsBcst**(1-CRRA), ShkPrbs)'
        E_Next_.uInv_permShk = E_Next_.uInv_permShk = eval(
            py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        E_Next_.uInv_permShk_fcts.update({'latexexpr': r'\ExuInvpermShk'})
        E_Next_.uInv_permShk_fcts.update({'urlhandle': r'ExuInvpermShk'})
        E_Next_.uInv_permShk_fcts.update({'py___code': py___code})
        E_Next_.uInv_permShk_fcts.update({'value_now': E_Next_.uInv_permShk})
        E_Next_.uInv_permShk_fcts = E_Next_.uInv_permShk_fcts

        GPFNrm_fcts = {
            'about': 'Normalized Expected Growth Patience Factor'
        }
        py___code = 'GPFRaw * E_Next_.Inv_permShk'
        Bilt.GPFNrm = eval(py___code, {},
                           {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        GPFNrm_fcts.update({'latexexpr': r'\GPFNrm'})
        GPFNrm_fcts.update({'_unicode_': r'Þ_Γ'})
        GPFNrm_fcts.update({'urlhandle': urlroot + 'GPFNrm'})
        GPFNrm_fcts.update({'py___code': py___code})
        Bilt.GPFNrm_fcts = GPFNrm_fcts

        GICNrm_fcts = {
            'about': 'Stochastic Growth Normalized Impatience Condition'
        }
        GICNrm_fcts.update({'latexexpr': r'\GICNrm'})
        GICNrm_fcts.update({'urlhandle': urlroot + 'GICNrm'})
        GICNrm_fcts.update({'py___code': 'test: GPFNrm < 1'})
        Bilt.GICNrm_fcts = GICNrm_fcts

        FVAC_fcts = {  # overwrites PF version
            'about': 'Finite Value of Autarky Condition'
        }

        FVAF_fcts = {  # overwrites PF version FVAFPF
            'about': 'Finite Value of Autarky Factor'
        }
        py___code = 'LivPrb * DiscLiv'
        Bilt.FVAF = eval(py___code, {},
                         {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        FVAF_fcts.update({'latexexpr': r'\FVAF'})
        FVAF_fcts.update({'urlhandle': urlroot + 'FVAF'})
        FVAF_fcts.update({'py___code': py___code})
        Bilt.FVAF_fcts = FVAF_fcts

        FVAC_fcts = {  # overwrites PF version
            'about': 'Finite Value of Autarky Condition'
        }
        FVAC_fcts.update({'latexexpr': r'\FVAC'})
        FVAC_fcts.update({'urlhandle': urlroot + 'FVAC'})
        FVAC_fcts.update({'py___code': 'test: FVAF < 1'})
        Bilt.FVAC_fcts = FVAC_fcts

        WRPF_fcts = {
            'about': 'Weak Return Patience Factor'
        }
        py___code = '(UnempPrb ** (1 / CRRA)) * RPF'
        Bilt.WRPF = WRPF = eval(py___code, {},
                                {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        WRPF_fcts.update({'latexexpr': r'\WRPF'})
        WRPF_fcts.update({'_unicode_': r'℘^(1/\rho) RPF'})
        WRPF_fcts.update({'urlhandle': urlroot + 'WRPF'})
        WRPF_fcts.update({'value_now': WRPF})
        WRPF_fcts.update({'py___code': py___code})
        Bilt.WRPF_fcts = WRPF_fcts

        WRIC_fcts = {
            'about': 'Weak Return Impatience Condition'
        }
        WRIC_fcts.update({'latexexpr': r'\WRIC'})
        WRIC_fcts.update({'urlhandle': urlroot + 'WRIC'})
        WRIC_fcts.update({'py___code': 'test: WRPF < 1'})
        Bilt.WRIC_fcts = WRIC_fcts

        DiscGPFNrmCusp_fcts = {
            'about': 'DiscFac s.t. GPFNrm = 1'
        }
        py___code = '((PermGroFac/E_Next_.Inv_permShk)**(CRRA))/Rfree'
        Bilt.DiscGPFNrmCusp = DiscGPFNrmCusp = \
            eval(py___code, {}, {**E_Next_.__dict__, **Bilt.__dict__, **givens})
        DiscGPFNrmCusp_fcts.update({'latexexpr': ''})
        DiscGPFNrmCusp_fcts.update({'value_now': DiscGPFNrmCusp})
        DiscGPFNrmCusp_fcts.update({'py___code': py___code})
        Bilt.DiscGPFNrmCusp_fcts = DiscGPFNrmCusp_fcts

    def build_facts_recursive(self):
        """
        Calculate recursive facts for current period from next.

        Returns
        -------
        soln_crnt : solution

        """
        super().build_facts_recursive()

        # All the recursive facts are required for PF model so already exist
        # But various lambda functions are interesting when uncertainty exists

        soln_crnt = self.soln_crnt
        Bilt = soln_crnt.Bilt
        Pars = soln_crnt.Pars
        E_Next_ = soln_crnt.E_Next_

        # To use these it is necessary to have created an alias to
        # the relevant namespace on the solution object, e.g.
        # E_Next_ = [soln].E_Next_
        # Bilt = [soln].Bilt
        # Pars = [soln].Pars

        # Given m, value of c where E[m_{t+1}]=m_{t}
        E_Next_.m_tp1_minus_m_t_eq_0 = (
            lambda m_t:
            m_t * (1 - 1 / E_Next_.RNrm) + (1 / E_Next_.RNrm)
        )
        # Given m, value of c where E[mLev_{t+1}/mLev_{t}]=Bilt.Pars.permGroFac
        # Solves for c in equation at url/#balgrostable
        E_Next_.permShk_times_m_tp1_minus_m_t_eq_0 = (
            lambda m_t:
            m_t * (1 - E_Next_.Inv_RNrm_PF) + E_Next_.Inv_RNrm_PF
        )
        # E[m_{t+1} pLev_{t+1}/pLev_{t}] as a fn of a_{t}
        E_Next_.mLev_tp1_Over_pLev_t_from_a_t = (
            lambda a_t:
            E_dot(Pars.PermGroFac *
                  Pars.permShkValsBcst *
                  (E_Next_.RNrm_PF / Pars.permShkValsBcst) * a_t
                  + Pars.tranShkValsBcst,
                  Pars.ShkPrbs)
        )
        # E[c_{t+1} pLev_{t+1}/pLev_{t}] as a fn of a_{t}
        E_Next_.cLev_tp1_Over_pLev_t_from_a_t = (
            lambda a_t:
            E_dot(Pars.PermGroFac *
                  Pars.permShkValsBcst *
                  Bilt.cFunc((E_Next_.RNrm_PF / Pars.permShkValsBcst) * a_t
                             + Pars.tranShkValsBcst),
                  Pars.ShkPrbs)
        )
        E_Next_.c_where_E_Next_m_tp1_minus_m_t_eq_0 = \
            lambda m_t: \
            m_t * (1 - 1 / E_Next_.RNrm) + (1 / E_Next_.RNrm)
        # Solve the equation at url/#balgrostable
        E_Next_.c_where_E_Next_permShk_times_m_tp1_minus_m_t_eq_0 = \
            lambda m_t: \
            (m_t * (1 - 1 / E_Next_.RNrm_PF)) + (1 / E_Next_.RNrm_PF)
        # mNrmTrg solves E_Next_.RNrm*(m - c(m)) + E[inc_next] - m = 0
        E_Next_.m_tp1_minus_m_t = (
            lambda m_t:
            E_Next_.RNrm * (m_t - Bilt.cFunc(m_t)) + E_Next_.IncNrmNxt - m_t
        )
        E_Next_.cLev_tp1_Over_pLev_t_from_num_a_t = (
            lambda a_t:
            E_dot(
                Pars.permShkValsBcst * Pars.PermGroFac * Bilt.cFunc(
                    (E_Next_.RNrm_PF / Pars.permShkValsBcst) *
                    a_t + Pars.tranShkValsBcst
                ),
                Pars.ShkPrbs)
        )
        E_Next_.cLev_tp1_Over_pLev_t_from_lst_a_t = (
            lambda a_lst: list(map(
                E_Next_.cLev_tp1_Over_pLev_t_from_num_a_t, a_lst
            ))
        )
        E_Next_.cLev_tp1_Over_pLev_t_from_a_t = (
            lambda a_t:
            E_Next_.cLev_tp1_Over_pLev_t_from_lst_a_t(a_t)
            if (type(a_t) == list or type(a_t) == np.ndarray) else
            E_Next_.cLev_tp1_Over_pLev_t_from_num_a_t(a_t)
        )
        E_Next_.cLev_tp1_Over_pLev_t_from_lst_m_t = (
            lambda m_t:
            E_Next_.cLev_tp1_Over_pLev_t_from_lst_a_t(m_t -
                                                      Bilt.cFunc(m_t))
        )
        E_Next_.cLev_tp1_Over_pLev_t_from_num_m_t = (
            lambda m_t:
            E_Next_.cLev_tp1_Over_pLev_t_from_num_a_t(m_t -
                                                      Bilt.cFunc(m_t))
        )
        E_Next_.cLev_tp1_Over_cLev_t_from_m_t = (
            lambda m_t:
            E_Next_.cLev_tp1_Over_pLev_t_from_lst_m_t(m_t) / Bilt.cFunc(m_t)
            if (type(m_t) == list or type(m_t) == np.ndarray) else
            E_Next_.cLev_tp1_Over_pLev_t_from_num_m_t(m_t) / Bilt.cFunc(m_t)
        )
        E_Next_.permShk_tp1_times_m_tp1_minus_m_t = (
            lambda m_t:
            E_Next_.RNrm_PF * (m_t - Bilt.cFunc(m_t)) + E_Next_.IncNrmNxt - m_t
        )

        self.soln_crnt = soln_crnt

        return soln_crnt


class ConsIndShockSolverBasic(ConsIndShockSetup):
    """
    Solves a single period of a standard consumption-saving problem.

    Uses linear interpolation and missing the ability to calculate the value
    function.  ConsIndShockSolver inherits from this class and adds the ability
    to perform cubic interpolation and to calculate the value function.

    Note that this class does not have its own initializing method. It initial-
    izes the same problem in the same way as ConsIndShockSetup, from which it
    inherits.
    """

    def make_chosen_state_grid(self):
        """
        Make grid of potential values of state variable(s) after choice(s).

        Parameters
        ----------
        none

        Returns
        -------
        aNrmGrid : np.array
            A 1D array of end-of-period assets; also is made attribute of Bilt.
        """
        # We define aNrmGrid all the way from BoroCnstNat up to max(aXtraGrid)
        # even if BoroCnstNat<BoroCnstArt, so we can construct the consumption
        # function as lower envelope of the (by the artificial borrowing con-
        # straint) unconstrained consumption function, and  artificially con-
        # strained consumption function.
        self.soln_crnt.Bilt.aNrmGrid = np.asarray(
            self.soln_crnt.Bilt.aXtraGrid) + self.soln_crnt.Bilt.BoroCnstNat

        return self.soln_crnt.Bilt.aNrmGrid

    # "Expectorate" = calculate expected values of useful objects across the
    # distribution of shocks at constructed grid of values of the relevant
    # states. I tried to invent a new usage, "Expectate" but Spyder/PyCharm
    # kept flagging it as not a real word, while now they don't complain!
    def make_E_Next_(self, IncShkDstn):
        """
        Expectorate after choices but before end of period (incl. discounting).

        Parameters
        ----------
        IncShkDstn : DiscreteDistribution
            The distribution of the stochastic shocks to income.
        """
        crnt, futr = self.soln_crnt, self.soln_futr

        Bilt, Pars, E_Next_ = crnt.Bilt, crnt.Pars, crnt.E_Next_

        eventTiming = Pars.eventTiming  # this_EOP or next_BOP (or both)

        states_chosen = Bilt.aNrmGrid
        permPos = IncShkDstn.parameters['ShkPosn']['perm']

        if eventTiming == 'EOP':  # shocks happen at end of this period
            CRRA = futr.Bilt.vFunc.CRRA  # Next CRRA utility normalizes
            Discount = Pars.DiscLiv  # Discount next period
            vFunc = futr.Bilt.vFunc
            cFunc = futr.Bilt.cFunc
            PermGroFac = futr.Pars.PermGroFac
            Rfree = Pars.Rfree
        else:  # default to BOP
            # In this case, we should have computed the 'hard part' and
            # attached it already to the BOP of the futr stage.
            breakpoint()
            # DiscLiv = Pars.DiscLiv
            # v0_pos, v1_pos = futr.Ante_E_.v0_pos, futr.Ante_E_.v1_pos
            # v2_pos = futr.Ante_E_.v2_pos

        # This is the efficient place to compute expectations of anything
        # at very low marginal cost by adding to list of things calculated

        def f_to_expect_across_shocks_given_current_state(xfer_shks_bcst,
                                                          states_chosen):
            next_choice_states = \
                self.transit_states_given_shocks(
                    'chosen_to_next_choice', states_chosen,
                    xfer_shks_bcst, IncShkDstn)
            mNrm = next_choice_states['mNrm']
            # Random shocks to permanent income affect mean PermGroFac
            PermGroFacShk = xfer_shks_bcst[permPos] * PermGroFac
            # expected value function derivatives 0, 1, 2
            v_0 = PermGroFacShk ** (1 - CRRA - 0) * vFunc(mNrm)
            v_1 = PermGroFacShk ** (1 - CRRA - 1) * vFunc.dm(mNrm) * Rfree
            v_2 = PermGroFacShk ** (1 - CRRA - 2) * vFunc.dm.dm(mNrm) * Rfree \
                * Rfree
            # cFunc derivatives 0, 1 (level and MPC); no need, but ~zero cost.
            c_0 = cFunc(mNrm)
            c_1 = cFunc.derivative(mNrm)
            return Discount * np.array([v_0, v_1, v_2, c_0, c_1])

        E_Next_.post_choice = np.squeeze(
            expect_funcs_across_shocks_given_states(
                IncShkDstn,
                f_to_expect_across_shocks_given_current_state,
                states_chosen)
        )
        # Store positions of the various objects for later convenience
        E_Next_.v0_pos, E_Next_.v1_pos, E_Next_.v2_pos = 0, 1, 2
        E_Next_.c0_pos, E_Next_.c1_pos = 4, 5

    def make_Ante_E_(self, IncShkDstn):
        """
        Expectorate before beginning-of-period events, and subsequent choices.

        Parameters
        ----------
        IncShkDstn : DiscreteDistribution
            The distribution of the stochastic shocks to income.
        """
        crnt = self.soln_crnt

        Bilt, Pars, Ante_E_ = crnt.Bilt, crnt.Pars, crnt.Ante_E_

        eventTiming = Pars.eventTiming  # this_EOP or next_BOP (or both)

        if eventTiming == 'EOP':  # shocks happen at end of this period
            return  # nothing needs to be done

        CRRA, Rfree, permPos = Pars.CRRA, Pars.Rfree, Pars.permPos
        PermGroFac = Pars.PermGroFac
        vFunc, cFunc = crnt.vFunc, crnt.cFunc
        Discount = 1.0  # Allows formulae to be identical here and in E_Next_
        BOP_state = Bilt.mNrmGrid

        # This is the efficient place to compute expectations of anything
        # at very low marginal cost by adding to list of things calculated

        def f_to_expect_across_shocks_given_current_state(xfer_shks_bcst,
                                                          BOP_state):
            choicestep_states = \
                self.transit_states_given_shocks(
                    'BOP_to_choice', BOP_state,
                    xfer_shks_bcst, IncShkDstn)
            mNrm = choicestep_states['mNrm']
            # Random shocks to permanent income affect mean PermGroFac
            PermGroFacShk = xfer_shks_bcst[permPos] * PermGroFac
            # expected value function derivatives 0, 1, 2
            v_0 = PermGroFacShk ** (1 - CRRA - 0) * vFunc(mNrm)
            v_1 = PermGroFacShk ** (1 - CRRA - 1) * vFunc.dm(mNrm) * Rfree
            v_2 = PermGroFacShk ** (1 - CRRA - 2) * vFunc.dm.dm(mNrm) * Rfree \
                * Rfree
            # cFunc derivatives 0, 1 (level and MPC); no need, but ~zero cost.
            c_0 = cFunc(mNrm)
            c_1 = cFunc.derivative(mNrm)
            return Discount * np.array([v_0, v_1, v_2, c_0, c_1])

        Ante_E_.ante_choice = np.squeeze(
            expect_funcs_across_shocks_given_states(
                IncShkDstn,
                f_to_expect_across_shocks_given_current_state,
                BOP_state)
        )
        # Store positions of the various objects for later convenience
        Ante_E_.v0_pos, Ante_E_.v1_pos, Ante_E_.v2_pos = 0, 1, 2
        Ante_E_.c0_pos, Ante_E_.c1_pos = 4, 5

    def build_cFunc_using_EGM(self):
        """
        Find interpolation points (c, m) for the consumption function.

        Parameters
        ----------
        none

        Returns
        -------
        cFunc : LowerEnvelope or LinerarInterp
        """
        crnt = self.soln_crnt
        Bilt, E_Next_, Pars = crnt.Bilt, crnt.E_Next_, crnt.Pars
        v1_pos = E_Next_.v1_pos  # first derivative of value function at chosen
        u, aNrmGrid, BoroCnstArt = Bilt.u, Bilt.aNrmGrid, Pars.BoroCnstArt

        # Endogenous Gridpoints steps
        # [v1_pos]: precalculated first derivative (E_Next_from_chosen_states)
        cNrmGrid = u.dc.Nvrs(E_Next_.post_choice[v1_pos])
        mNrmGrid = aNrmGrid + cNrmGrid

        # Limiting consumption is zero as m approaches BoroCnstNat
        mPlus, cPlus = (
            np.insert(mNrmGrid, 0, Bilt.BoroCnstNat, axis=-1),
            np.insert(cNrmGrid, 0, 0.0, axis=-1))  # c = 0 at BoroCnstNat

        # Store these for future use
        Bilt.cNrmGrid, Bilt.mNrmGrid = cNrmGrid, mNrmGrid

        interpolator = self.make_cFunc_linear  # default is piecewise linear

        if self.CubicBool:
            interpolator = self.make_cFunc_cubic

        # Use the given interpolator to construct the consumption function
        cFuncUnc = interpolator(mPlus, cPlus)  # Unc = Unconstrained (this prd)

        # Combine constrained and unconstrained functions into the true cFunc
        # by choosing the lower of the constrained and unconstrained functions
        # LowerEnvelope should only be used when BoroCnstArt is true
        if BoroCnstArt is None:
            cFunc = cFuncUnc
        else:
            # CDC 20210614: LinearInterp and LowerEnvelope are both handmade
            # We should substitute standard ways to do these things
            # EconForge interpolation.py or scipy.interpolate for interpolation
            Bilt.cFuncCnst = LinearInterp(
                np.array([Bilt.mNrmMin, Bilt.mNrmMin + 1.0]),
                np.array([0.0, 1.0]))
            cFunc = LowerEnvelope(cFuncUnc, Bilt.cFuncCnst, nan_bool=False)

        return cFunc

    def make_t_decision_rules_and_value_functions(self):
        """
        Construct consumption function and marginal value function.

        Given the grid of end-of-period values of assets a, use the endogenous
        gridpoints method to obtain the corresponding values of consumption,
        and use the dynamic budget constraint to obtain the corresponding value
        of market resources m.

        Parameters
        ----------
        none (relies upon self.soln_crnt.aNrmGrid to exist at invocation)

        Returns
        -------
        solution : ConsumerSolution
            The solution to the single period consumption-saving problem.
        """
        crnt = self.soln_crnt
        Bilt, Pars = crnt.Bilt, crnt.Pars

        crnt.cFunc = Bilt.cFunc = self.build_cFunc_using_EGM()
        crnt = def_value_funcs(crnt, Pars.CRRA)

        return crnt

    def make_cFunc_linear(self, mNrm, cNrm):
        """
        Make linear interpolation for the (unconstrained) consumption function.

        Parameters
        ----------
        mNrm : np.array
            Corresponding market resource points for interpolation.
        cNrm : np.array
            Consumption points for interpolation.

        Returns
        -------
        cFunc_unconstrained : LinearInterp
            The unconstrained consumption function for this period.
        """
        cFunc_unconstrained = LinearInterp(
            mNrm, cNrm,
            self.soln_crnt.Bilt.cFuncLimitIntercept,
            self.soln_crnt.Bilt.cFuncLimitSlope
        )
        return cFunc_unconstrained

    def from_chosen_states_make_continuation_E_Next_(self):
        """
        Expect after choices have been made and before shocks realized.

        Calculate circumstances of an agent before the realization of the labor
        income shocks that constitute transition to the next period's state.

        Resulting solution object contains the value function vFunc and
        its derivatives.  Does not calculate consumption function cFunc:
        that is a consequence of vFunc.da but is calculated in the
        stage that calls this one.

        Parameters
        ----------
        none (all should be on self)

        Returns
        -------
        solution : ConsumerSolution object
            Contains info (like vFunc.da) required to construct consumption
        """
        soln_crnt = self.soln_crnt

        # Add some useful info to solution object
        # CDC 20200428: "useful" only for a candidate converged solution
        # in an infinite horizon model.  It's virtually costless to compute but
        # usually there would not be much point in computing it for a
        # non-final infhor stage or finhor.  Exception: Better to construct
        # finhor LiqConstr without MaxKinks but user might want to know
        # these facts
        # TODO: Distinguish between those things that need to be computed for
        # "useful" computations in the final stage, and just info,
        # and make mandatory only the computations of the former category
        self.build_facts_infhor()
        self.build_facts_recursive()  # These require solution to successor

        soln_crnt = self.make_chosen_state_grid()
        self.make_E_Next_(self.soln_crnt.Pars.IncShkDstn)

        return soln_crnt

    def solve_prepared_stage(self):  # solve ONE stage (ConsIndShockSolver)
        """
        Solve one period of the consumption-saving problem.

        The ".Bilt" namespace on the returned solution object includes
            * decision rule (consumption function), cFunc
            * value and marginal value functions vFunc and vFunc.dm
            * a minimum possible level of normalized market resources mNrmMin
            * normalized human wealth hNrm; bounding MPCs MPCmin and MPCmax.

        If the user sets `CubicBool` to True, cFunc is interpolated with a
        cubic Hermite interpolator.  This is much smoother than the default
        piecewise linear interpolator, and causes (automatic) construction of
        the marginal marginal value function vFunc.dm.dm (which occurs
        automatically).

        In principle, the resulting cFunc will be numerically incorect at
        values of market resources where a marginal increment to m would
        discretely change the probability of a future constraint binding,
        because in principle cFunc is nondifferentiable at those points (cf
        LiqConstr REMARK).

        In practice, if the distribution of shocks is not too granular, the
        approximation error is minor.

        Parameters
        ----------
        none (all should be on self)

        Returns
        -------
        solution : agent_solution
            The solution to this period/stage's problem.
        """

        if self.solve_prepared_stage_divert():  # Allow bypass of normal soln
            return self.soln_crnt  # created by solve_prepared_stage_divert

        crnt = self.soln_crnt

        Pars = crnt.Pars
        eventTiming, solveMethod = Pars.eventTiming, Pars.solveMethod

        if solveMethod == 'Generic':  # Steps that should encompass any problem
            self.define_transition(crnt, 'EOP_to_next_BOP')
            self.define_transition(crnt, 'chosen_to_EOP')
            self.from_chosen_states_make_continuation_E_Next_(crnt)
            self.define_t_reward(crnt, def_utility_CRRA)
            self.define_transition(crnt, 'choice_to_chosen')
            self.make_t_decision_rules_and_value_functions(crnt)
            self.define_transition(crnt, 'BOP_to_choice')
            self.from_BOP_states_make_Ante_E_(crnt)
            return crnt

        # if not using Generic, then solve using custom method

        # transition depends on whether events are EOP or BOP
        if eventTiming == 'EOP':  # Events (shocks, etc) at End of Problem
            define_transition(crnt, 'chosen_to_next_choice')
        else:
            define_transition(crnt, 'chosen_to_next_BOP')

        # Given the transition, calculate expectations
        self.from_chosen_states_make_continuation_E_Next_()

        # Define transition caused by choice
        define_transition(crnt, 'choice_to_chosen')

        # Define today's reward (utility, in consumer's problem)
        define_t_reward(crnt, def_utility_CRRA)

        # Having calculated E(marginal value, etc) of saving, construct c and v
        self.make_t_decision_rules_and_value_functions()

        # Ante_E_: Before BOP shocks realized (does nothing if no BOP shocks)
        self.from_BOP_states_make_Ante_E_()

        return crnt

    # alias "solve" because core.py expects [agent].solve to solve the model
    solve = solve_prepared_stage  # much easier to remember

    def from_BOP_states_make_Ante_E_(self):
        """Make expectations before beginning-of-period events (shocks)."""
        self.make_Ante_E_(self)

    def transit_states_given_shocks(self, transition_name, starting_states,
                                    shks_permuted, IncShkDstn):
        """
        From starting_states calculate transitions given shock permutations.

        Return array of values of normalized market resources m
        corresponding to permutations of potential realizations of
        the permanent and transitory income shocks, given the value of
        end-of-period assets aNrm.

        Parameters
        ----------
        shks_permuted: 2D ndarray
            Permanent and transitory income shocks in 2D ndarray

        aNrm: float
            Normalized end-of-period assets this period

        Returns
        -------
        transited : dict with results of applying transition eqns
        """
        stge = self.soln_crnt
        Pars = stge.Pars
        Transitions = stge.Modl.Transitions

        permPos, tranPos = (
            IncShkDstn.parameters['ShkPosn']['perm'],
            IncShkDstn.parameters['ShkPosn']['tran'])

        zeros = starting_states - starting_states  # zero array of right size

        xfer_vars = {
            'permShk': shks_permuted[permPos] + zeros,  # + zeros fixes size
            'tranShk': shks_permuted[tranPos] + zeros,  # all pmts for each st
            'aNrm': starting_states
        }

        # Everything needed to execute the transition equations
        Info = {**Pars.__dict__, **xfer_vars}

        transition = Transitions[transition_name]
        for eqn_name in transition['compiled'].keys():
            exec(transition['compiled'][eqn_name], Info)

        return Info


class ConsIndShockSolver(ConsIndShockSolverBasic):
    """
    Solves a single period of a standard consumption-saving problem.

    Inherits from ConsIndShockSolverBasic; adds cubic interpolation.
    """

    def make_cFunc_cubic(self, mNrm_Vec, cNrm_Vec):
        """
        Make cubic spline interpolation of unconstrained consumption function.

        Requires self.soln_crnt.Bilt.aNrm to have been computed already.

        Parameters
        ----------
        mNrm_Vec : np.array
            Corresponding market resource points for interpolation.
        cNrm_Vec : np.array
            Consumption points for interpolation.

        Returns
        -------
        cFuncUnc : CubicInterp
            The unconstrained consumption function for this period.
        """
        soln_crnt = self.soln_crnt
        Bilt, E_Next_ = soln_crnt.Bilt, soln_crnt.E_Next_
        v2_pos = E_Next_.v2_pos  # second derivative of value function
        u = Bilt.u

        dc_da = E_Next_.post_choice[v2_pos] / u.dc.dc(np.array(cNrm_Vec[1:]))
        MPC = dc_da / (dc_da + 1.0)
        MPC = np.insert(MPC, 0, Bilt.MPCmax)

        cFuncUnc = CubicInterp(
            mNrm_Vec, cNrm_Vec, MPC, Bilt.MPCmin *
            Bilt.hNrm, Bilt.MPCmin
        )
        return cFuncUnc