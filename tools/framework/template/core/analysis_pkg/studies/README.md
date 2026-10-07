# studies

One notebook per physics study, written like a page of a lab notebook: say what you
set out to learn, show what you ran, and write down what you concluded.

Notebooks here import the analysis (`from analysis_pkg.tools import ...`); they do not
define cuts or histograms of their own. If a study needs a new cut, histogram or
object, add it to `analysis_pkg/definitions/` so the next study can use it too.
