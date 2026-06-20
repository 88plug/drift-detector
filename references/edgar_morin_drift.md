# Edgar Morin and the Problem of AI Behavioral Drift

*A reading of la pensée complexe against the drift-detector plugin's current design.*

## Why Morin, and why here

The drift detector, as it stands, is a beautiful piece of *restricted* complexity.
`src/lib/drift_score.py` counts hedges, filler, hype, and meta-narration, ramps
verbosity, length, and long-word ratio into 0–1 components, and folds them through
a weighted noisy-OR into a single 0–100 number. It is deterministic, stdlib-only,
and total — it never raises. It is, in Morin's vocabulary, a masterpiece of
*disjunction, reduction, and abstraction*: it isolates the symptom (lexical
relapse), measures it in isolation, and abstracts a contract-violation into a
scalar. Morin spent six volumes of *La Méthode* (1977–2004) arguing that this is
exactly how complex phenomena are misunderstood. He distinguished **restricted
complexity** — keep classical epistemology, add cleverer techniques — from
**general complexity**, which demands a change in *what we count as knowing*. The
drift detector lives entirely in the former. The interesting question is what the
latter would build.

## 1. The seven principles, applied to a drifting model

Morin's seven operators of complex thinking are: **systemic/organizational**,
**hologrammatic**, **retroactive loop** (feedback), **recursive loop**,
**self-eco-organization** (autonomy/dependence), **dialogic**, and **reintroduction
of the knowing subject**. Read against a model that wanders from its behavioral
contract:

- *Systemic*: drift is not a property of a single turn (which is all the detector
  scores) but an emergent property of the whole conversation-plus-system-prompt
  organization. The whole is not the sum of the turns; the qualities that drift
  (tone, register, compliance) only exist at the level of the relationship.
- *Hologrammatic*: each turn contains the whole disposition of the model, and the
  whole session is inscribed in each turn. A single relapse is not noise to be
  averaged away — it is the whole system showing its hand in miniature.
- *Retroactive loop*: a verbose reply lowers the bar; the next reply regresses
  toward it. Drift is self-reinforcing through context.
- *Recursive loop*: the products of drift become the producers of more drift —
  the cybernetic core of the whole problem (see §3).
- *Self-eco-organization*: the model is autonomous (its trained disposition) AND
  dependent on its environment (the prompt, the user, the context window). It
  cannot be understood as either alone.
- *Dialogic*: order (the contract) and disorder (the trained tendency to help)
  are both *necessary* and permanently antagonistic (§2).
- *Reintroduction of the subject*: there is no view from nowhere. The detector's
  thresholds, lexicons, and weights are a *human* judgment dressed as a
  measurement. The observer is in the observation.

## 2. The dialogic principle: drift as feature

Morin's favorite example of the dialogic was European culture as the
"complementary antagonism" of Judaeo-Christian and Greco-Roman strands — a complex
unity in which the duality *never resolves*. The drift detector implicitly treats
the contract ("be terse, in-character") as order and the model's helpful
disposition as disorder to be suppressed. Morin would refuse that hierarchy. The
helpfulness *is* what makes the assistant useful; the contract is what makes it
fit-for-purpose. They are complementary and antagonistic at once. A caveman that
has perfectly suppressed all helpfulness has not "stopped drifting" — it has died
of order. Drift, in this reading, is not a bug but the visible trace of a living
tension. The right design goal is not zero drift but a *productive* oscillation
that stays inside a viability envelope — exactly the kind of thing a scalar
"DRIFT 98%" badge cannot express, because it has already decided which pole is bad.

## 3. The recursive loop: outputs that re-make the maker

Morin's recursion is sharper than feedback: "the generating loop in which products
and effects produce what produces them." Feedback modifies a process; recursion
*re-produces the producer*. Claude's drifted outputs are not merely fed back into
the same conversation — at the population scale they become preference data,
RLHF signal, fine-tuning corpus. The base model that defines what "drift" even
*means* is itself partly a product of past drift that got rewarded. The contract
the detector measures against is therefore not fixed bedrock; it is a moving
artifact co-produced by the very behavior being measured. Morin would model this
as a **recursive loop spanning two timescales**: a fast loop inside one session
(context → reply → context) and a slow loop across training generations
(aggregate behavior → reward model → base disposition → aggregate behavior). A
detector blind to the slow loop will keep recalibrating to a baseline that is
itself drifting, and mistake a moving reference for a stable one.

## 4. Ecology of action: the anti-drift prompt that causes drift

This is the principle most directly threatening to the plugin's premise. Morin:
"the moment an individual undertakes an action, it starts to escape their
intentions… it enters a universe of interaction and it is finally the environment
that seizes it, in a direction that may become contrary to the initial intention."
A system prompt is an *action*, not a guarantee. Once launched into the context
window it interacts with everything else there — the user's tone, the task, the
model's priors, the accumulated history — and its effect is no longer the author's
to dictate. A more aggressive anti-drift instruction ("NEVER hedge, NEVER add
preamble, this is CRITICAL") is precisely the kind of action that can boomerang:
it raises the salience of the banned behavior, consumes attention budget,
introduces an adversarial register that itself reads as un-caveman, and can
trigger over-correction that the lexical scorer cannot even see. The detector's
own nudge — injected on the next turn when the previous reply broke contract — is
itself an action subject to the same ecology. Morin's lesson is not *don't act*;
it is act as **strategy** (multiple scenarios, redefined in flight) rather than as
**program** (one trajectory, asserted once and trusted). The plugin currently
nudges as a program.

## 5. Living systems: the detector that destabilizes by fighting too hard

Morin draws relentlessly on biology — auto-organization, homeostasis, the
Chilean autopoiesis of Maturana and Varela. The decisive insight: a living system
maintains its identity *through* continuous change, not by freezing. A cell holds
its form while every molecule in it is replaced. Homeostasis is not stasis; it is
dynamic regulation around a setpoint, and it depends on perturbation to function.
A regulator with infinite gain — one that drives every deviation instantly to
zero — does not stabilize a system; it oscillates it into destruction, or it
locks it rigid and brittle. A drift detector that fights every micro-deviation
hard (high `sensitivity`, low `threshold`, an aggressive nudge every turn) is
exactly such a high-gain controller. It risks turning a self-correcting living
register into a clenched, twitchy, over-managed one — which a human reader
experiences as a *different* and worse kind of unnatural than the original drift.
Morin would predict that the cure, applied too forcefully, produces a new
pathology the original metric is blind to.

## 6. Unitas multiplex: which self is the real Claude?

*Unitas multiplex* — unity and multiplicity at once — is Morin's answer to "which
is the real one." The model is one set of weights AND a manifold of behavioral
modes; the caveman, the helpful assistant, the terse engineer are not masks over a
true face but facets of a single complex unity, none of which is privileged. The
detector's design quietly assumes a "real" compliant self that drift corrupts, and
a "fake" verbose self to be punished. Morin dissolves the question: there is no
authentic mode to recover, only a unity that expresses different multiplicities
under different ecological conditions. Practically, this argues against a single
canonical profile-as-ground-truth and toward representing the assistant as a
*distribution* over modes, where "drift" is movement of the distribution, not
deviation from one anointed point.

## 7. What a Morin-designed detector would actually be

Almost certainly **not** a lexical classifier, because that is reduction by
construction — it severs the marker from the meaning. A complex-thinking detector
would be **relational, recursive, and self-aware**:

- It would score the *trajectory*, not the turn — drift as a vector over the
  conversation (direction and velocity of movement), not a snapshot scalar.
- It would be **dialogic**: report the *tension* between contract-fidelity and
  task-helpfulness as two coordinates, refusing to collapse them into one number
  that has already picked a winner.
- It would be **self-eco-organizing**: model the contract as a moving reference it
  re-estimates from the session's own established baseline, not a fixed lexicon.
- It would **reintroduce the subject**: surface its own thresholds and weights as
  contestable human choices, and let the user steer the *envelope*, not just the
  knob.
- It would prefer **homeostatic regulation** (gentle, proportional, occasional)
  over high-gain suppression.

## 8. Well-ordered perturbation: amplify some drift

Morin (after von Foerster's "order from noise" and Atlan's "complexity from
noise") held that systems grow by being *productively disturbed* — the
"perturbation bien ordonnée." Not all drift is decay. A caveman that loosens into
one clear explanatory sentence at exactly the moment the user is confused has
*adapted*, not failed. A rigid detector scores that adaptation as a 40-point hype
spike and nudges it back into uselessness. The complex-thinking move is to
distinguish **degenerative drift** (slow entropic relapse into generic verbosity,
no information) from **adaptive drift** (a context-appropriate, reversible
departure that serves the task). The latter should arguably be *tolerated or even
amplified*; suppressing it is suppressing the system's capacity to respond. This
requires the detector to read *context and intent*, not just lexical surface —
which the current engine, by design (`strip_code`, token counting), cannot.

## 9. Against reductionism: the critique in one paragraph

The current detector commits the three sins Morin named. **Disjunction**: it
strips code, isolates prose, and scores the turn cut off from the conversation
that gives it meaning. **Reduction**: it reduces "stopped following instructions"
to marker density and sentence length — a hedge inside a careful caveat about a
real risk is scored identically to a hedge that is pure relapse. **Abstraction**:
it abstracts a living, context-bound relationship into a context-free scalar with
a threshold. The noisy-OR is genuinely the right *local* aggregation choice — any
single strong signal should be able to fire — but no aggregation rule rescues
inputs that have already been severed from their relations. A complex-thinking
detector would re-inject what reduction threw away: the surrounding turns, the
task, the user's reaction, and the detector's own influence on the next reply.

## 10. Practical takeaways for the plugin

Five concrete changes Morin's framework motivates, each implementable on top of
the existing engine rather than replacing it:

1. **Ship a trajectory metric, not just a turn score.** Keep `analyze()` per-turn,
   but expose drift *velocity* — the slope of the score over the last N turns.
   A rising slope at low absolute score is the real early warning; a one-off spike
   often isn't drift at all. This is the systemic/recursive principle made cheap.
2. **Make the badge dialogic — two numbers, not one.** Show contract-fidelity and
   task-engagement as a pair (e.g. `caveman | fit 88 | help 72`). Refusing to
   collapse them stops the tool from declaring helpfulness the enemy, and it
   surfaces the "died of order" failure mode the single scalar hides.
3. **Re-estimate the baseline from the session (self-eco-organization).** Calibrate
   `target_words`/`target_wps` against the user's *own* early, in-contract turns
   instead of fixed constants. The reference should adapt to the established
   register, per §3's moving-reference problem.
4. **Lower the gain; nudge as strategy, not program (ecology of action).** Make the
   nudge proportional, occasional, and gentle — fire on sustained trajectory, not
   on a single threshold crossing — to avoid the high-gain destabilization of §5
   and the boomerang of §4. Prefer one quiet reminder over a per-turn drumbeat.
5. **Tolerate adaptive drift; flag only degenerative drift.** Add a cheap
   reversibility/context signal (did the departure track a user question or
   confusion?) so a context-appropriate loosening isn't punished like entropic
   relapse (§8). At minimum, do not nudge when a single departure immediately
   self-corrects.

The throughline: the current detector is correct *for what it measures* and wrong
*about what drift is*. Morin would not throw it out — restricted complexity has its
uses — but he would refuse to let the scalar have the last word. Drift is a living
tension to be regulated, not an error to be zeroed. Build the tool to hold the
contradiction, not to win it.

---

*Sources consulted: redalyc.org survey of Morin's paradigm of complexity (the
seven principles); CNRS News, "Edgar Morin: In praise of complex thought"; GRAIN,
"The Ecology of Action"; Cairn.info, "Vers une écologie de l'action complexe."
Morin's primary works: *La Méthode* (6 vols., 1977–2004), *Introduction à la
pensée complexe* (1990), *Les Sept savoirs nécessaires à l'éducation du futur*
(1999).*
