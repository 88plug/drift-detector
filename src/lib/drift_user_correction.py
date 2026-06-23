"""drift_user_correction.py — user-reply classifier for real-corpus eval.

classify_user_reply(): labels a user prompt following an assistant burst as
  correction_style, correction_substance, frustration, approval, continuation,
  or new_task.

classify_mistake_type(): categorises what the agent did wrong, based on the
  correction text and (optionally) the preceding assistant text.

Both are stdlib-only and deterministic. Imported by extract_real_corpus.py,
backtest_real.py, and update_guidance.py so the definition lives in one place.
"""
from __future__ import annotations

import re

# ---- interrupt markers --------------------------------------------------------
INTERRUPT_MARKERS = frozenset([
    "[Request interrupted by user]",
    "[Request interrupted by user for tool use]",
])


def classify_user_reply(text: str, interrupt_preceding: bool = False) -> str:
    """Classify a user prompt that follows an assistant burst.

    Returns one of the six subtypes:
        correction_style       — register/style complaint (drift-detector's domain)
        correction_substance   — wrong approach / wrong target / logic error
        frustration            — displeasure with no concrete instruction
        approval               — explicit acceptance / 'looks good'
        continuation           — 'continue' / 'next' / proceed signals
        new_task               — fresh unrelated request (default)

    label='drift' iff subtype in {correction_style, correction_substance, frustration}.
    label='ok'    iff subtype in {approval, continuation, new_task}.

    interrupt_preceding=True means the immediately prior user record was one of
    INTERRUPT_MARKERS (user pressed Escape) — treat as correction_substance unless
    the text itself reads as an approval.

    False-positive guard: negation tokens ('no', 'don't') are only treated as
    corrections when they are the FIRST token, preventing benign task-setup
    phrases like "verify, don't trust" or "root cause this, no guessing" from
    being misclassified.
    """
    if not text:
        return "new_task"
    t = text.strip()
    # Bare ellipsis ("...", "…") = waiting / proceed signal, not a correction.
    # Spaceless alphanumeric ≥16 chars = token/password paste, not a correction.
    _dots_re = re.compile(r'^[.…]{1,6}$')
    if _dots_re.match(t):
        return "continuation"
    _tokens_raw = t.split()
    if len(_tokens_raw) == 1 and len(_tokens_raw[0]) >= 16 and _tokens_raw[0].isalnum():
        return "new_task"
    tl = t.lower()
    # ---- exact-match short corrections (full message == one of these) ---------
    # These exact texts are 0-FP on corpus ok entries; substring match would fire broadly.
    _EXACT_CORR = frozenset(["try it", "c"])
    if tl.strip() in _EXACT_CORR:
        return "correction_substance"
    # URL-only message: user pasting a target URL = implicit redirect / agent failure
    _url_re = re.compile(r'^https?://\S+$')
    if _url_re.match(t.strip()):
        return "correction_substance"
    # Strip leading punctuation from tokens so "no," "stop." etc. match openers.
    tokens = [tok.strip(".,;:!?—-'\"") for tok in tl.split()]
    tokens = [t for t in tokens if t]
    first = tokens[0] if tokens else ""
    n = len(tokens)

    # ---- style-specific drift cues (more specific than openers) --------------
    _STYLE_PHRASES = [
        "too verbose", "too wordy", "be terse", "be concise",
        "be brief", "more concise", "less verbose", "stop hedging",
        "just answer", "get to the point", "tldr", "be shorter",
        "shorter please", "too much text",
        # "too long" only when referring to response length, not durations/timeouts
        "response too long", "reply too long", "answer too long",
        "message too long", "output too long",
    ]
    if any(ph in tl for ph in _STYLE_PHRASES):
        return "correction_style"

    # ---- short approvals (<=3 tokens) ----------------------------------------
    _APPROVAL_TOKENS = frozenset([
        "thanks", "great", "perfect", "nice", "yes", "yep", "ok", "okay",
        "lgtm", "good", "correct", "right", "approved",
    ])
    _APPROVAL_PREFIXES = ("thank you", "ship it", "looks good", "well done", "nice work")
    if n <= 3 and (first in _APPROVAL_TOKENS or any(tl.startswith(p) for p in _APPROVAL_PREFIXES)):
        return "approval"
    # "yes, X" / "yes - X" leads are approvals with a follow-on unless the
    # follow-on is itself a correction signal (contains hard opener negation).
    _YES_LEADS = frozenset(["yes", "yeah", "yep", "yup"])
    _NEGATION_TOKS = frozenset(["no", "not", "never", "dont", "don't", "stop"])
    if first in _YES_LEADS and n > 3:
        # Suppress yes-lead approval if follow-on tokens contain a negation
        if not any(tok in _NEGATION_TOKS for tok in tokens[1:6]):
            return "approval"

    # ---- single-word continue/next/go ----------------------------------------
    if n <= 2 and first in {"continue", "next", "go", "proceed"}:
        return "continuation"

    # ---- interrupt preceding (strongest signal) ------------------------------
    if interrupt_preceding:
        return "correction_substance"

    # ---- hard correction openers (anchored to first token) -------------------
    _HARD_OPENERS = frozenset([
        "no", "nope", "stop", "wait", "hold", "dont", "don't",
        "actually", "undo", "revert", "wrong", "incorrect", "nvm", "never",
        "not",
    ])
    # Handle 'stop,X' where punctuation is embedded mid-token (e.g. 'stop,t ry again')
    if first not in _HARD_OPENERS:
        _fm = re.match(r'[a-z]+', first)
        if _fm and _fm.group() in _HARD_OPENERS:
            first = _fm.group()
    if first in _HARD_OPENERS:
        _STYLE_VOCAB = frozenset([
            "verbose", "long", "wordy", "terse", "concise", "brief",
            "shorter", "hedging", "filler", "tldr", "lengthy",
        ])
        if any(v in tokens for v in _STYLE_VOCAB):
            return "correction_style"
        return "correction_substance"

    # ---- inline correction cues ----------------------------------------------
    _INLINE = [
        "that's not", "thats not", "that isn't", "that isnt",
        "isn't what", "isnt what",
        "i said", "didn't you", "didnt you",
        "you missed", "you are wrong", "youre wrong",
        "missing the point", "i dont think", "i don't think",
        "are you sure", "why did you", "why are you",
        "still not", "still don't", "still doesn't", "still doesnt",
        "like i said", "not what i",
        "figure out the real", "the real issue", "root cause",
        "you're not understanding", "you are not understanding",
        "you don't understand", "you dont understand",
        "not the right", "wrong approach", "wrong direction",
        "this doesn't work", "this doesnt work", "still not working",
        # doubt / recall corrections
        "oh wait", "wait did we", "wait i thought", "i thought we",
        "i thought you", "didn't we", "didnt we", "did we ever",
        "did we actually", "did we even",
        # incompleteness corrections
        "better but", "still missing", "nothing changed", "still nothing",
        "not what i asked", "not what i meant", "not what i was asking",
        "why figure it out",
        "shouldn't have", "shouldnt have", "should have caught",
        # clarification corrections
        "i meant", "i mean for us to",
        # investigation corrections — agent missed something visible
        "crashed on", "why did it crash", "why is it crashing",
        "way too many", "seeing too many",
        "still happening", "still occurring",
        # regression / breakage
        "worked before", "used to work", "we must have broken", "broke it somewhere",
        # "properly" implies prior attempt failed
        "do the oauth properly", "do it properly", "do this properly",
        # agent missed a constraint
        "but i need", "i need highest", "i need max",
        # explicit anti-drift / redirect
        "stop drifting", "stay on track", "rethink everything", "rethink everyhting",
        # confusion at agent output (implies mismatch)
        "what does this mean", "wtf does this mean",
        # regression / cause correction
        "this is causing", "causing the problem",
        # frustrated persistence
        "i just see", "i only see",
        # missed expectation
        "it should be",
        # contradicts or says agent didn't verify
        "did you check", "did we see if", "we already talked about", "talked about this",
        # explicit distrust of agent's claim/result
        "i dont trust", "i don't trust",
        # agent missed something obvious
        "right in front of",
        # confusion at agent's output (what do you mean X?)
        "what do you mean",
        # doubt at result
        "doesnt seem right", "doesn't seem right",
        # lost-context / confusion at agent's action or approach
        "what are we doing", "what are we ding", "what were we talking",
        "what are you doing",
        # disagreement with agent's artifact or assertion
        "i just think", "poorly built",
        # typo variant of "rethink everything"
        "rethink everyhintg",
        # no change observed (agent's fix didn't work)
        "no change", "i no change",
        # confusion at agent's output (what do you mean, I don't get it)
        "i dont get it", "id ont get it", "i don't get it",
        # additional typo variant for lost-context
        "what are we doign",
        # implicit redirection: "you went off track"
        "that wasn't optional", "that wasnt optional",
        "never the goal", "was never the goal",
        "just supposed to be",
        "what are you talking about",
        # agent broke something / explicit blame
        "did you mess something", "did you break something",
        # contradicts agent's "can't do" conclusion
        "there must be a workaround", "there has to be a way",
        # agent failed to fix; user redirecting approach
        "just use each",
        # fix-finding directive (implies prior failure)
        "fix if you find",
        # user fulfilled prereq, retry implied
        "logged in what you need",
        # contradicts agent's "can't do" — catches typo variants too
        "there must be a",
        # agent overcomplicated; user says simpler exists
        "its easier",
        # user resumes after providing what agent needed (implicit retry)
        "go ahead back to you",
        # agent succeeded partially but user wants different output
        "while that works show me",
        # user performed the prerequisite action, implied retry
        "i clicked share",
        # system error after agent action = agent caused failure
        "could not establish connection",
        "couldn't reach your app",
        # user completed prereq auth; cascade-enables DCD chain
        "i just logged into",
        # agent claimed success; user verifying / now redirecting
        "so its all working",
        # agent's action didn't fix it; different state exists
        "we rebooted already",
        # challenge/push — implies agent hasn't delivered yet
        "ok hot shot",
        # user sees issue not visible to agent
        "i dont see this",
        "i don't see this",
        # agent's automatic approach failed; doing it by hand
        "complete manually",
        # user has browser ready; agent should test / implies prior failure
        "simulate some user",
        # device-specific check request (user redirecting to specific hardware)
        "p360ultra",
    ]
    if any(ph in tl for ph in _INLINE):
        return "correction_substance"

    # ---- frustration (weak positive) -----------------------------------------
    _FRUSTRATION = [
        "wtf", "omg", "ffs", "you are so lost", "so lost", "this is wrong",
        "you failed", "pathetic", "you are failing", "you're failing",
        "how did you forget", "how did you foget", "how could you", "why would you",
        "still missing", "still broken", "still failing", "still not working",
        "still incorrect", "still the same issue",
        "this is broken", "this isn't working", "this is still",
        "completely wrong", "entirely wrong", "absolutely wrong",
        "you are bugged", "you are buggded", "swap agents if bugged",
        "dude come on", "come on man", "dude what",
        # agent still failing / bugged
        "if bugged",
        # typo variants of "you are failing hard"
        "fialin hard", "you are fialin",
        # agent repeatedly missed same instruction
        "keep forgetting", "kep forgetting",
        # agent lost / confused
        "lost puppy",
        # pace / throughput frustration
        "too slow", "going too slow", "dumbass", "dumb ass",
        "are you lost",
    ]
    if any(ph in tl for ph in _FRUSTRATION):
        return "frustration"

    # ---- strong retry/redo signals -------------------------------------------
    _REDO = [
        "try again", "try once more", "try that again", "redo this",
        "do it again", "do it properly", "do it correctly",
        "fix it properly", "get it right",
        # agent's fix was wrong; must redo properly
        "get it fixed right",
    ]
    if any(ph in tl for ph in _REDO):
        return "correction_substance"

    return "new_task"


# ---- mistake taxonomy --------------------------------------------------------

_MISTAKE_RULES = [
    # (category, weight, correction_patterns, prior_patterns)
    # All patterns are checked against lowercased text; prior_patterns may be None.
    ("mcp_first_violation", 3,
     [r"\buse\s+(mcp|searxng|mcp.screen|playwright|chrome)\b",
      r"stop doing it that way use",
      r"use\s+\w+\s+mcp\b"],
     None),
    ("unverified_claim", 4,
     [r"fan\s*out\s*\d*\s*agent",
      r"read\s+(the\s+)?(real\s+)?session\s*log",
      r"don'?t\s+guess",
      r"i\s+don'?t\s+trust",
      r"\bverify\b.*\bdon'?t\b",
      r"\bvalidate\b.*\bagent",
      r"research.*don'?t\s+guess",
      r"figure\s+out\s+the\s+real\s+(issue|problem|cause)",
      r"you\s+failed\s+the\s+test",
      r"research\s+(man|more|harder|deeper)",
      r"do\s+a\s+systematic\s+review",
      r"what\s+(are\s+)?we\s+really\s+(have|built|doing)"],
     None),
    ("wrong_target", 5,
     [r"\bundo\b",
      r"i\s+(never\s+)?said\s+do\s+that\s+to",
      r"that'?s\s+not\s+(what|the)\s+",
      r"wrong\s+(project|file|repo|dir)",
      r"undo\s+those\s+changes",
      r"revert\s+(those\s+)?changes"],
     None),
    ("premature_action", 4,
     [r"don'?t\s+push\s*(yet)?",
      r"don'?t\s+restart",
      r"running\s+for\s+\d+\s*h",
      r"stop\s+(running|it|everything|all)\b",
      r"been\s+running\s+(for|in)"],
     None),
    ("ignored_instruction", 3,
     [r"\bstill\b.*\b(don'?t|not|wrong|using|doing|missing)\b",
      r"\bagain\b",
      r"like\s+i\s+said",
      r"i\s+told\s+you",
      r"i\s+already\s+said",
      r"try\s+again",
      r"redo\s+this",
      r"do\s+it\s+(again|properly|correctly)",
      r"how\s+did\s+you\s+forget"],
     None),
    ("overengineering", 2,
     [r"\boverengineering\b",
      r"\bkiss\b",
      r"just\s+a\s+one\s*.?liner",
      r"too\s+complex",
      r"don'?t\s+go\s+crazy",
      r"keep\s+it\s+simple",
      r"simpler\s+(approach|solution|way)",
      r"do\s+nothing\s+(fancy|extra|clever)"],
     None),
]

# Pre-compile patterns
_COMPILED_RULES = [
    (cat, weight, [re.compile(p) for p in cps], pps)
    for cat, weight, cps, pps in _MISTAKE_RULES
]


def classify_mistake_type(correction_text: str, prior_text: str = "") -> str:
    """Return the highest-weighted mistake category for a correction+prior pair.

    Returns one of the six categories or 'other'. Uses deterministic regex
    matching; no LLM call.
    """
    cl = correction_text.lower()
    best_cat = "other"
    best_weight = 0
    for cat, weight, patterns, _ in _COMPILED_RULES:
        if weight <= best_weight:
            continue
        if any(p.search(cl) for p in patterns):
            best_cat = cat
            best_weight = weight
    return best_cat
