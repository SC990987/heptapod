# tests

The chain report: a regression check for the analysis chain, meant to run on every
pull request.

```bash
python tests/chain_report.py compute state.json          # run the chain, write its state
python tests/chain_report.py render base.json new.json   # compare two states
```

`compute` runs every channel and every histogram collection over the small files in
`tests/fixtures.yaml` and records the static consistency checks, the cutflows (raw and
weighted), the histograms that stayed empty, and every warning the processor recorded.
It runs the processor with `strict=False`, so that one failing cut is reported next to
everything else instead of hiding it.

`render` compares two such states and prints what moved, as markdown. Its exit status
is 1 in two cases:

- the newer state has **new errors**: a config newly refers to a cut, object or
  histogram that does not exist; the chain newly crashes on a fixture; or a cut,
  object, weight, histogram or counter newly fails at run time. A failure the older
  state already had is shown but not counted;
- the chain could not be run on a fixture at all, even if it fails the same way on
  the older state: then nothing was compared. This includes having no fixture
  registered yet, so the check is red until the first one is added. A report that ran
  nothing must not look like a pass.

Changed cutflows, new warnings of the "is not available in this sample" kind and newly
empty histograms are shown but do not fail the check: whether they are intended is for
the reviewer to say.

The workflow in `.github/workflows/chain-report.yml` computes the state of the pull
request and of the branch it targets, and writes the comparison to the job summary.

## What it does not tell you

That the physics is right. A cut that runs cleanly can still be the wrong cut, and a
fixture of a few hundred events only sees the selections that keep some of them. The
report says where to look; it does not replace looking.

## Fixtures

```bash
python tests/make_fixture.py /path/to/signal.root --dataset MySignal --year 2018 \
    --events 200 --skim-factor 0.5
python tests/make_fixture.py /path/to/data.root --dataset MyData --data --year 2018 \
    --first-event 11649 --events 200
```

`--year` names a run period of `configs/run_periods.yaml`: without it simulation is not
scaled (its weighted columns stay sums of generator weights) and data finds no golden
JSON. The script lowers `chunksize` in `tests/fixtures.yaml` when needed, so that every
fixture is read in at least two chunks.

Fixtures are committed, so keep them to a few hundred events. Choose them with care:

- **Simulation**: a slice in which the main selections keep at least a few events. A
  channel that ends at zero events cannot show a regression downstream of that point.
- **Data**: a slice that crosses a boundary of the golden JSON, so that with the
  configured chunk size one chunk is emptied completely. Empty chunks are a classic
  source of crashes that simulation never exercises.
- **skim_factor**: something other than 1 for simulation. The normalisation divides by
  it, and at exactly 1 a mistake in its handling changes nothing.

The dataset name is what the fixture is processed as, so a simulated fixture needs a
cross section under that name in `configs/cross_sections.yaml` for the weighted
columns to be scaled.
