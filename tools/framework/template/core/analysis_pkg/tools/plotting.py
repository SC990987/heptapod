"""Plotting helpers built on mplhep.

Kept apart from utilities so that importing the processor never pulls in matplotlib.
"""

import warnings

import hist
import hist.intervals
import matplotlib.pyplot as plt
import mplhep as hep
import numpy as np

# Which experiment's style and label to use: any name mplhep knows ("CMS", "ATLAS",
# "LHCb", "ALICE", ...).
EXPERIMENT = "__EXPERIMENT__"


def set_plot_style(style=None, dpi=50):
    """Use the experiment's mplhep style."""
    style = EXPERIMENT if style is None else style
    for name in (style, style.upper()):
        if hasattr(hep.style, name):
            plt.style.use(getattr(hep.style, name))
            break
    else:
        raise NotImplementedError(f"mplhep has no style called '{style}'")
    plt.rcParams["figure.dpi"] = dpi


def add_label(data=False, **kwargs):
    """Draw the experiment label on the current axes (does nothing if mplhep has none).

    data: what is plotted includes real data. The label of a plot of simulation alone
        says "Simulation", which is mplhep's default and this function's.
    Other keyword arguments go to mplhep's label function (``lumi``, ``year``, ...).
    """
    module = getattr(hep, EXPERIMENT.lower(), None)
    if module is not None and hasattr(module, "label"):
        return module.label(data=data, **kwargs)
    return None


def plot(hists, skip_label=False, yerr=None, data=False, **kwargs):
    """Plot a 1D or 2D histogram (or a list of 1D histograms) with mplhep.

    skip_label: do not draw the experiment label. When several histograms are drawn
        on the same axes one call after another, skip it for all and call
        ``add_label`` once.
    yerr: error bars for a 1D histogram. Pass it explicitly for any derived quantity
        (an efficiency, a ratio, a scale factor), where the default sqrt(N) bars mean
        nothing; ``get_eff_hist`` returns the matching errors. Leave it as None for an
        ordinary histogram of counts.
    data: the histogram holds real data (see ``add_label``).
    """
    kwargs = {"flow": "sum", **kwargs}
    dim = len(hists[0].axes) if isinstance(hists, list) else len(hists.axes)
    if dim == 1:
        artists = hep.histplot(hists, yerr=yerr, **kwargs)
    elif dim == 2:
        artists = hep.hist2dplot(hists, **kwargs)
    else:
        raise NotImplementedError(f"cannot plot a {dim}-dimensional histogram")
    if not skip_label:
        add_label(data=data)
    return artists


def _variances(histogram):
    """Per-bin variances of a histogram, refusing one that did not record them."""
    variances = histogram.variances()
    if variances is None:
        raise TypeError(
            "this histogram was filled with weights into a storage that does not keep "
            "their variance, so no errors can be computed for it: define it with "
            'storage="weight" (the default of tools.histogram.Histogram)')
    return variances


def get_eff_hist(num_hist, denom_hist):
    """Efficiency histogram num/denom and its (2, N) array of down/up errors.

    Meant for the case where the numerator is a subset of the denominator. Plot the
    result with ``plot(eff, yerr=errors, histtype="errorbar")``.

    The intervals are computed from effective counts (sum of weights squared over sum
    of squared weights), which makes them sensible for weighted histograms. With
    weights that differ a lot from event to event, the effective count of a genuine
    subset can come out larger than that of the whole: hist then raises a ValueError.
    """
    denom_vals = denom_hist.values()
    num_vals = num_hist.values()
    num_var, denom_var = _variances(num_hist), _variances(denom_hist)
    with np.errstate(divide="ignore", invalid="ignore"):
        eff_values = num_vals / denom_vals
        num_counts = num_vals ** 2 / num_var
        denom_counts = denom_vals ** 2 / denom_var
    eff_hist = hist.Hist(*num_hist.axes, storage=hist.storage.Weight())
    eff_hist.view().value[:] = np.nan_to_num(eff_values)
    errors = hist.intervals.ratio_uncertainty(num_counts, denom_counts, "efficiency")
    # symmetrised interval as the variance, so the histogram is usable without yerr too
    eff_hist.view().variance[:] = np.nan_to_num(0.5 * (errors[0] + errors[1])) ** 2
    return eff_hist, errors


def get_ratio_hist(num_hist, denom_hist):
    """Bin-by-bin ratio with Gaussian error propagation, for ratios that are not efficiencies."""
    num, den = num_hist.values(), denom_hist.values()
    num_var, den_var = _variances(num_hist), _variances(denom_hist)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(den != 0, num / den, np.nan)
        rel2 = (np.where(num != 0, num_var / num ** 2, 0.0)
                + np.where(den != 0, den_var / den ** 2, 0.0))
        err = np.nan_to_num(np.abs(ratio) * np.sqrt(rel2))
    ratio_hist = hist.Hist(*num_hist.axes, storage=hist.storage.Weight())
    ratio_hist.view().value[:] = np.nan_to_num(ratio)
    ratio_hist.view().variance[:] = err ** 2
    return ratio_hist, np.array([err, err])


def plot_ratio(num, den, efficiency=True, labels=None, ratio_ylabel=None, ratio_ylim=None,
               xlim=None, ylim=None, ylabel=None, data=False):
    """Two-panel plot: the histograms on top, num/den underneath.

    num: histogram or list of histograms; den: the reference histogram
    efficiency: treat num as a subset of den (binomial intervals); otherwise the
        errors are propagated as for independent histograms. If the intervals cannot
        be computed for a histogram (see ``get_eff_hist``), its ratio is drawn with
        the errors of independent histograms instead, and a warning says so.
    labels: legend entries, the denominator first
    data: the histograms hold real data (see ``add_label``)
    """
    nums = num if isinstance(num, list) else [num]
    labels = list(labels) if labels else [None] * (len(nums) + 1)
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(12, 12), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0},
    )
    plt.sca(top)
    plot(den, flow="none", color="k", skip_label=True, label=labels[0])
    for h, label in zip(nums, labels[1:]):
        plot(h, flow="none", skip_label=True, label=label)
    add_label(data=data)
    if any(label is not None for label in labels):
        top.legend()
    if ylim is not None:
        top.set_ylim(*ylim)
    if ylabel is not None:
        top.set_ylabel(ylabel)

    plt.sca(bottom)
    for h in nums:
        if efficiency:
            try:
                ratio, errors = get_eff_hist(h, den)
            except ValueError as exc:
                warnings.warn(
                    "efficiency intervals could not be computed (the numerator is not a "
                    f"subset of the denominator, or the weights vary too much: {exc}); the "
                    "ratio is drawn with the errors of two independent histograms",
                    stacklevel=2)
                ratio, errors = get_ratio_hist(h, den)
        else:
            ratio, errors = get_ratio_hist(h, den)
        plot(ratio, histtype="errorbar", yerr=errors, flow="none", skip_label=True)
    bottom.set_ylabel(ratio_ylabel or ("Efficiency" if efficiency else "Ratio"))
    if ratio_ylim is None and efficiency:
        ratio_ylim = (0, 1.2)
    if ratio_ylim is not None:
        bottom.set_ylim(*ratio_ylim)
    if xlim is not None:
        bottom.set_xlim(*xlim)
    return fig, (top, bottom)


def plot_samples(out, name, channel, samples=None, density=False, **kwargs):
    """Overlay one histogram for several samples of a processor output."""
    samples = list(out) if samples is None else list(samples)
    hists = [out[sample]["hists"][name][{"channel": channel}] for sample in samples]
    # the label says "Simulation" unless one of the samples is data
    kwargs.setdefault("data", any(True in out[sample]["metadata"]["is_data"] for sample in samples))
    artists = plot(hists, label=samples, density=density, **kwargs)
    plt.legend()
    return artists
