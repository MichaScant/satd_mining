"""Graphs: AI noticing vs revisiting its own technical debt, with circumstances."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

OUT = Path(__file__).resolve().parent / "graphs"; OUT.mkdir(exist_ok=True)
ELI, SPO = "#3b7dd8", "#e07b39"


def save(fig, name):
    fig.tight_layout(); fig.savefig(OUT / name, dpi=140, facecolor="white"); plt.close(fig); print("saved", name)


def trigger_bar(data, title, n, fname):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    labels = [d[0] for d in data][::-1]; vals = [d[1] for d in data][::-1]
    colors = [SPO if "spontaneous" in l else ELI for l in labels]
    b = ax.barh(labels, vals, color=colors)
    for bar, v in zip(b, vals):
        ax.text(v + max(vals)*0.01, bar.get_y()+bar.get_height()/2, f"{v} ({100*v/n:.0f}%)", va="center", fontsize=9)
    ax.set_title(title, fontweight="bold"); ax.set_xlabel("instances"); ax.margins(x=0.16)
    save(fig, fname)


# 1. noticing triggers (119)
trigger_bar([("dev raised concern/question",36),("dev named debt",33),("dev alluded to error",27),
             ("neutral dev comment before",16),("changes_requested",0),("spontaneous",7)],
            "AI NOTICING its own debt — circumstances  (n=119)", 119, "adm_notice_triggers.png")

# 2. revisiting triggers (150)
trigger_bar([("dev named debt",56),("dev raised concern/question",41),("dev alluded to error",26),
             ("neutral dev comment before",16),("changes_requested",5),("spontaneous",6)],
            "AI REVISITING (removing) its own debt — circumstances  (n=150)", 150, "adm_revisit_triggers.png")

# 3. noticing vs revisiting — elicited vs spontaneous
fig, ax = plt.subplots(figsize=(6.5, 4.3))
acts = ["Noticing\n(admitting)", "Revisiting\n(removing)"]
eli = [112, 144]; spo = [7, 6]
ax.bar(acts, eli, 0.5, label="elicited (developer-prompted)", color=ELI)
ax.bar(acts, spo, 0.5, bottom=eli, label="spontaneous (no prompt)", color=SPO)
for i,(e,s) in enumerate(zip(eli,spo)):
    ax.text(i, e/2, f"{e}\n{100*e/(e+s):.0f}%", ha="center", va="center", color="white", fontweight="bold")
    ax.text(i, e+s+2, f"spont {s} ({100*s/(e+s):.0f}%)", ha="center", fontsize=9, color=SPO)
ax.set_title("Both acts are overwhelmingly developer-elicited", fontweight="bold")
ax.set_ylabel("dialogue instances"); ax.legend(fontsize=9); ax.margins(y=0.12)
save(fig, "adm_notice_vs_revisit.png")

# 4. code SATD by phase
fig, ax = plt.subplots(figsize=(7, 4))
ph=["first_attempt\n(spontaneous)","revision\n(elicited)","human_applied\n(not agent)"]
pv=[35,702,2100]; pc=[SPO,ELI,"#9aa0a6"]
bb=ax.bar(ph,pv,color=pc)
for bar,v in zip(bb,pv): ax.text(bar.get_x()+bar.get_width()/2,v,f"{v}\n{100*v/2837:.0f}%",ha="center",va="bottom",fontsize=9)
ax.set_title("Agent code-comment SATD by commit phase  (n=2,837)", fontweight="bold")
ax.set_ylabel("instances"); ax.margins(y=0.15)
save(fig, "adm_code_by_phase.png")
print("done ->", OUT)
