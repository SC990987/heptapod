from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


BASE = Path(__file__).resolve().parents[1]


def load(name):
    return np.load(BASE / name)


plt.style.use("seaborn-v0_8-whitegrid")

mass = load("sidm_full_selection_ljlj_mass_2mu2e.npy")
fig, ax = plt.subplots(figsize=(8, 5))
ax.hist(mass, bins=50, range=(0, 1000), histtype="stepfilled", alpha=0.75,
        color="#337ab7", edgecolor="#174a73")
ax.axvline(500, color="#c0392b", linestyle="--", linewidth=1.7,
           label=r"Generated $M_{B_s}=500$ GeV")
ax.axvline(np.median(mass), color="#2c3e50", linestyle=":", linewidth=1.7,
           label=f"Median = {np.median(mass):.2f} GeV")
ax.set(xlim=(0, 1000), xlabel=r"$m_{LJ,LJ}$ [GeV]", ylabel="Events",
       title=r"SIDM $2\mu2e$ channel: reconstructed bound-state mass")
ax.legend(frameon=True)
fig.tight_layout()
fig.savefig(BASE / "sidm_full_ljlj_mass_2mu2e.png", dpi=180)
plt.close(fig)

panels = [
    ("sidm_full_genkin_dp_pt.npy", r"Dark-photon $p_T$", r"$p_T$ [GeV]", None),
    ("sidm_full_genkin_dp_eta.npy", r"Dark-photon $\eta$", r"$\eta$", None),
    ("sidm_full_genkin_dphi.npy", r"Dark-photon pair $\Delta\phi$", r"$\Delta\phi$ [rad]", np.pi),
    ("sidm_full_genkin_lxy.npy", r"Dark-photon $L_{xy}$", r"$L_{xy}$ [cm]", 30.0),
]
fig, axes = plt.subplots(2, 3, figsize=(13, 7.5))
for ax, (name, title, xlabel, reference) in zip(axes.flat, panels):
    values = load(name)
    ax.hist(values, bins=50, histtype="stepfilled", alpha=0.72,
            color="#337ab7", edgecolor="#174a73")
    if reference is not None:
        ax.axvline(reference, color="#c0392b", linestyle="--", linewidth=1.5)
    ax.set(title=title, xlabel=xlabel, ylabel="Entries")

ax = axes.flat[4]
for name, label, color in [
    ("sidm_full_genkin_dR_ee.npy", r"$e^+e^-$", "#337ab7"),
    ("sidm_full_genkin_dR_mm.npy", r"$\mu^+\mu^-$", "#c0392b"),
]:
    ax.hist(load(name), bins=50, histtype="step", linewidth=1.7,
            label=label, color=color)
ax.set(title=r"Di-lepton $\Delta R$", xlabel=r"$\Delta R(\ell^+,\ell^-)$", ylabel="Entries")
ax.legend(frameon=True)

axes.flat[5].axis("off")
fig.suptitle("SIDM generator-level kinematics", y=0.995)
fig.tight_layout()
fig.savefig(BASE / "sidm_full_gen_kinematics.png", dpi=180)
plt.close(fig)
