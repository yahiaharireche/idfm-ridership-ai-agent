# Evaluation

Measures how good the agent's answers are, on a fixed set of questions, in a
way that can be repeated after every change.

## Run it

```
.venv/bin/python evaluation/run.py baseline     # the reference scores
.venv/bin/python evaluation/run.py <label>      # after a change: compared with baseline
.venv/bin/python evaluation/calibrate_judge.py  # check the judge still agrees (about $0.001)
```

Scores go to `evaluation/results/<label>.json`, kept in git. Every answer and
trace goes to `runs/`, which is not.

## What is measured

`questions.py` holds 32 questions in six groups: the three users the product
is for (planner, analyst, communications), traps in the data, questions that
must be refused, and robustness (typos, French, attempts to break the rules).
Each question is asked twice, to measure consistency.

Each answer is graded twice:

1. **Automatic checks** (`grade.py`), exact and free: the right numbers are
   present, the right percentage, the warning named, the refusal made, the
   right language. The expected numbers are not typed in: each question
   carries the SQL that computes them, run on the database every time.
2. **A judge**: a second model, from a different family than the one
   answering, reads the answer next to reference notes and says whether it is
   correct, passes on the warnings that matter, and is honest.

An answer passes when every check passes and the judge agrees on all three.

## Trusting the judge

A judge can be wrong. `calibrate_judge.py` gives it six answers whose verdict
is known, modelled on real mistakes a model can make. It agreed
on the pass or fail verdict in all six; on two it was stricter than the first
labels (an answer that wrongly claims a total is "complete" is also not
honest), and the labels were changed to its reading. Before relying on a new
score, read a few of the failed answers in `runs/` yourself.

The judge's thinking was later set to "low": with its default thinking it
sometimes used all its tokens and gave no verdict (8 of 64 in the first
baseline). The six calibration cases were not re-run after that change, for
budget reasons; re-run `calibrate_judge.py` before relying on new scores.

## Results (October 2026, DeepSeek V4.1 Flash)

| | passed |
|---|---|
| `baseline`: all 32 questions, 2 runs each | 61/64 (95.3%) |
| the 10 questions re-run after the fixes, before | 17/20 |
| the same 10 questions, after (`after_fix`) | 19/20 |

The fixes: the last step forces an answer (two runs had ended with no answer);
outside knowledge must be labelled as such (a strike was given as the cause
of a drop, as if the data showed it); answer once the question is covered.

Four questions planned for the re-run were not asked, because the budget ran
out: `busiest_line`, `solidarite`, `forecast_2027`, `injection`. They passed in
the baseline but were not re-checked after the prompt changes.

What the first baseline taught about the evaluation itself: three of its
reference answers were wrong and the agent was right (Gare de Lyon's
weighted share, the 2015 unattributed taps, the Solidarité relabelling), and
one quality warning in the data model mixed two periods. A test set needs
checking as much as the agent does. `results/baseline_first_grading.json`
keeps the scores from before those corrections.

## Budget

`budget.json` sets what the whole phase may spend. `run.py` checks it against
OpenRouter's own count for the key, and only starts a question if the budget
can still pay for it and for every question already running at their worst
case, so it cannot be overshot.
