# studies

One notebook per physics study, written like a page of a lab notebook: say what you
set out to learn, show what you ran, and write down what you concluded.

Notebooks here import the analysis (`from analysis_pkg.tools import ...`); they do not
define cuts or histograms of their own. If a study needs a new cut, histogram or
object, add it to `analysis_pkg/definitions/` so the next study can use it too.

`obj_basics/basic_histograms.ipynb` is the first, written with the framework: it runs
the `baseline` channel over a file of two simulated samples and one data sample, prints
the cuts and the cutflow, and draws the pT and multiplicity of the muons and electrons
(of the first two objects, without them) with the samples overlaid. Copy it to start a
new study; its figures go to `output/studies/` at the top of the repository.
