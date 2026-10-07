"""What the analysis is: its objects, cuts, histograms and weights.

- objects.py : the collections the analysis works with, and where each comes from
- cuts.py    : every available object-level and event-level cut, by name
- hists.py   : every available histogram and counter, by name
- weights.py : the weight each event enters histograms and cutflows with

These modules only *define* things. Which cuts form a selection and which histograms
form a collection is chosen by name in configs/selections.yaml and
configs/hist_collections.yaml.
"""
