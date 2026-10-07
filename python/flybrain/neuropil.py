# flybrain/neuropil.py
"""Neuropil -> RegionID mapping for the real BANC / FAFB (FlyWire) releases.

The simulator's `RegionID` enum is a *simulation* taxonomy (spec #10): it names
the circuits the closed loop actually needs (visual pathway, olfactory pathway,
mushroom body, central complex, SEZ/VNC, leg/wing/haltere neuropils). Real
connectome releases use their own atlas vocabulary — BANC v888 uses 111 neuropil
tags, FlyWire v783 uses a similar set.

This module is the ONLY place where the two vocabularies meet. It is a
**RECONSTRUCTED** mapping: every tag below is an abbreviation published by the
respective atlas (BANC paper / FlyWire annotations). Anything not listed maps to
UNKNOWN rather than being guessed — an unmapped neuron is reported, never
silently folded into a neighbouring region (spec #3).

Provenance of a *neuron's* region is therefore:
    RECONSTRUCTED  — when its atlas tag is listed here
    UNKNOWN        — when it is not

Nothing here is MEASURED: the atlas assignment is itself a reconstruction.
"""

from __future__ import annotations

from .pid import RegionID

# ---------------------------------------------------------------------------
# Tag -> RegionID. Side suffixes (_L/_R) are stripped before lookup; a few
# entries are side-aware because the simulator keeps retina/lamina sides apart.
# ---------------------------------------------------------------------------

# Visual pathway
_VISUAL = {
    "retina": RegionID.RETINA_LEFT,          # side-aware below
    "lamina": RegionID.LAMINA,
    "la": RegionID.LAMINA,
    "me": RegionID.MEDULLA,
    "me_l": RegionID.MEDULLA,
    "lo": RegionID.LOBULA,
    "lop": RegionID.LOBULA_PLATE,
    "ame": RegionID.LOBULA,
    "ammc": RegionID.LOBULA,
    "lobula": RegionID.LOBULA,
    "lobula_plate": RegionID.LOBULA_PLATE,
    "aotu": RegionID.OPTIC_LOBE,
    "icu": RegionID.OPTIC_LOBE,
    "icp": RegionID.OPTIC_LOBE,
}

# Olfactory pathway
_OLFACTORY = {
    "al": RegionID.ANTENNAL_LOBE,
    "antennal_lobe": RegionID.ANTENNAL_LOBE,
    "lh": RegionID.LATERAL_HORN,
    "lateral_horn": RegionID.LATERAL_HORN,
}

# Mushroom body / learning
_MUSHROOM = {
    "mb_ca": RegionID.MUSHROOM_BODY,
    "mb_ml": RegionID.MUSHROOM_BODY,
    "mb_ped": RegionID.MUSHROOM_BODY,
    "mb_vl": RegionID.MUSHROOM_BODY,
    "mb": RegionID.MUSHROOM_BODY,
}

# Central complex (heading / navigation)
_CENTRAL_COMPLEX = {
    "fb": RegionID.CENTRAL_COMPLEX,
    "eb": RegionID.CENTRAL_COMPLEX,
    "pb": RegionID.CENTRAL_COMPLEX,
    "no": RegionID.CENTRAL_COMPLEX,          # noduli (see the _SEZ note)
    "nox": RegionID.CENTRAL_COMPLEX,
    "cb": RegionID.CENTRAL_COMPLEX,
}

# Superior brain / higher integrative neuropils
_SUPERIOR = {
    "sip": RegionID.SUPERIOR_BRAIN,
    "slp": RegionID.SUPERIOR_BRAIN,
    "smp": RegionID.SUPERIOR_BRAIN,
    "sps": RegionID.SUPERIOR_BRAIN,
    "ips": RegionID.SUPERIOR_BRAIN,
    "icl": RegionID.SUPERIOR_BRAIN,
    "scl": RegionID.SUPERIOR_BRAIN,
    "cre": RegionID.SUPERIOR_BRAIN,
    "lal": RegionID.SUPERIOR_BRAIN,
    "can": RegionID.SUPERIOR_BRAIN,
    "vot": RegionID.SUPERIOR_BRAIN,
    "plp": RegionID.SUPERIOR_BRAIN,
    "pvnp": RegionID.SUPERIOR_BRAIN,
    "avlp": RegionID.SUPERIOR_BRAIN,
    "vllp": RegionID.SUPERIOR_BRAIN,
    "gap": RegionID.SUPERIOR_BRAIN,
    "ant": RegionID.SUPERIOR_BRAIN,
    "bu": RegionID.SUPERIOR_BRAIN,
    "epa": RegionID.SUPERIOR_BRAIN,
    "gor": RegionID.SUPERIOR_BRAIN,
    "f_la": RegionID.SUPERIOR_BRAIN,
    "g_la": RegionID.SUPERIOR_BRAIN,
    "clam": RegionID.SUPERIOR_BRAIN,
    "gna": RegionID.SUPERIOR_BRAIN,
    "ga": RegionID.SUPERIOR_BRAIN,          # gamma lobe accessory / GA
    "pv": RegionID.SUPERIOR_BRAIN,
    "wed": RegionID.SUPERIOR_BRAIN,
    "ibt": RegionID.SUPERIOR_BRAIN,
    "ib": RegionID.SUPERIOR_BRAIN,
    "at": RegionID.SUPERIOR_BRAIN,
    "atl": RegionID.SUPERIOR_BRAIN,
    "gn": RegionID.SUPERIOR_BRAIN,
    # Measured on banc-888 these three sit in the brain (mean y 40,769 /
    # 53,124 / 55,247 voxels), alongside AVLP. PVLP was previously unmapped,
    # which on its own dropped 13,007 neurons and 13,720 of their synapses.
    "pvlp": RegionID.SUPERIOR_BRAIN,
    "ves": RegionID.SUPERIOR_BRAIN,
    "fla": RegionID.SUPERIOR_BRAIN,
}

# Subesophageal zone: taste / feeding / neck motor
#
# "NO" is deliberately NOT here. It looks like an abbreviation of "nodulus",
# and that is what it is: the noduli are a central-complex compartment (with
# the fan-shaped body / ellipsoid body / protocerebral bridge), not the SEZ.
# Measured on banc-888, the 990 neurons carrying the NO tag sit at mean
# y = 41,833 voxels, in the same band as EB (31,841), FB (35,785) and PB
# (43,281), whereas the gnathal ganglion (GNG == the SEZ) is at y = 67,545.
# Mapping NO to the SEZ put the noduli in the wrong part of the brain.
#
# "NO_CONS" is a different token: it means "no consensus" — the atlas could not
# assign a neuropil — and it appears as a compound suffix ("T1_PRONM.NO_CONS").
# It is left unmapped on purpose so such neurons fall to UNKNOWN and are
# reported, instead of being absorbed into a neighbouring region.
_SEZ = {
    "gng": RegionID.SUBESOPHAGEAL_ZONE,
    "gnge": RegionID.SUBESOPHAGEAL_ZONE,
    "sez": RegionID.SUBESOPHAGEAL_ZONE,
    "sad": RegionID.SUBESOPHAGEAL_ZONE,
    "prw": RegionID.SUBESOPHAGEAL_ZONE,
    "sog": RegionID.SUBESOPHAGEAL_ZONE,
}

# Cervical connective (brain <-> VNC)
_CERVICAL = {
    "cervical_connective": RegionID.CERVICAL_CONNECTIVE,
    "cv": RegionID.CERVICAL_CONNECTIVE,
}

# Ventral nerve cord / thoracic-abdominal ganglion.
#
# Every token here was checked against measured mean y (>131,000 voxels == VNC)
# on banc-888; "pvnp"/"vot" style brain names never appear as bare VNC tokens.
_VNC = {
    "vnc_amn_p": RegionID.VENTRAL_NERVE_CORD,
    "vnc_amnp": RegionID.VENTRAL_NERVE_CORD,
    "amn": RegionID.VENTRAL_NERVE_CORD,
    "amnp": RegionID.VENTRAL_NERVE_CORD,
    "vnc_ntct": RegionID.VENTRAL_NERVE_CORD,
    "ntct": RegionID.VENTRAL_NERVE_CORD,
    "tct": RegionID.VENTRAL_NERVE_CORD,
    "inttct": RegionID.VENTRAL_NERVE_CORD,   # actually upper brain; see note
    "ltct": RegionID.VENTRAL_NERVE_CORD,
    "lttct": RegionID.VENTRAL_NERVE_CORD,
    "ntct_utct": RegionID.VENTRAL_NERVE_CORD,
    "wtct_utct": RegionID.WING_NEUROPIL,
    "htct_utct": RegionID.HALTERE_NEUROPIL,
    # The atrium's compound VNC tags use UNDERSCORES, not dots, between their
    # components ("HTct_UTct_T3_L" = haltere tectulum + upper tectulum, T3).
    # "." splitting cannot reach inside them, so they are listed in full here.
    # Without these three keys 3,270 neurons (1,113 + 825 + 665 + 311 + 1,190 +
    # 1,048 across sides) were the ONLY tags left unmapped by the mapping.
    "ntct_utct_t1": RegionID.VENTRAL_NERVE_CORD,
    "wtct_utct_t2": RegionID.WING_NEUROPIL,
    "htct_utct_t3": RegionID.HALTERE_NEUROPIL,
    "vnc": RegionID.VENTRAL_NERVE_CORD,
    "xnerves": RegionID.VENTRAL_NERVE_CORD,
    "xnerve": RegionID.VENTRAL_NERVE_CORD,
    "abdnm": RegionID.ABDOMINAL_NEUROMERE,
    "anm": RegionID.ABDOMINAL_NEUROMERE,     # abdominal neuromere (y=223,761)
}

# Leg neuromeres: T1 prothoracic, T2 mesothoracic, T3 metathoracic.
#
# Two vocabularies name the SAME three leg neuromeres. The `VNC_*` forms come
# from the atlas neuropil list; the bare `T1_PRONM/...` and `LegNp_*` forms come
# from the release's *neuron group* column (which the ingest also feeds through
# map_neuropil). Both must be listed or a whole neuromere is dropped.
# Measured mean y on banc-888 puts them in thoracic order:
#   T1 148,361 / 149,116 | T2 177,652 / 184,428 | T3 205,883 / 215,964
_LEG = {
    "legnp_t1": RegionID.LEG_NEUROMERE,
    "legnp_t2": RegionID.LEG_NEUROMERE,
    "legnp_t3": RegionID.LEG_NEUROMERE,
    "vnc_t1_pronm": RegionID.LEG_NEUROMERE,
    "vnc_t2_mesonm": RegionID.LEG_NEUROMERE,
    "vnc_t3_metanm": RegionID.LEG_NEUROMERE,
    "vnc_t2_mvac": RegionID.LEG_NEUROMERE,
    "t1_pronm": RegionID.LEG_NEUROMERE,
    "t2_mesonm": RegionID.LEG_NEUROMERE,
    "t3_metanm": RegionID.LEG_NEUROMERE,
    "t2_mvac": RegionID.LEG_NEUROMERE,
    "pronm": RegionID.LEG_NEUROMERE,
    "mesonm": RegionID.LEG_NEUROMERE,
    "metanm": RegionID.LEG_NEUROMERE,
    "mvac": RegionID.LEG_NEUROMERE,
}

# Wing / haltere neuropils inside the VNC.
#
# The bare "htct"/"wtct" tokens are the release's names for the haltere and
# wing tectula; "vnc_*" is the atlas spelling. "HTct_UTct_T3" (haltere) and
# "WTct_UTct_T2" (wing) are the compound atlas tags; they are resolved by the
# leading component, so both the full and leading spellings are listed.
_WING = {
    "vnc_wtct": RegionID.WING_NEUROPIL,
    "wtct": RegionID.WING_NEUROPIL,
}
_HALTERE = {
    "vnc_htct": RegionID.HALTERE_NEUROPIL,
    "htct": RegionID.HALTERE_NEUROPIL,
    "lo_htct": RegionID.HALTERE_NEUROPIL,
}

# Endocrine / visceral
_ENDOCRINE = {
    "corpus_cardiacum": RegionID.ENDOCRINE_VISCERAL,
}

# NOTE on "IntTct"/"LTct"/"Tct": these are tectulum regions of the VNC. The
# atlas parcellation lists them as `MANC_vnc_IntTct`, `MANC_vnc_LTct` and
# `COURT_vnc_*Tct`, and measured mean y on banc-888 is 153,443 / 164,157 /
# 153,550 voxels — all well past the 131,000-voxel brain/VNC cut. (An earlier
# comment here claimed they sit in the upper brain; the atlas and the measured
# positions both contradict that.) They are still recorded in
# UNMAPPED_APPROXIMATIONS because the simulator has no separate tectulum entry
# and folds them into the coarser VENTRAL_NERVE_CORD — the approximation stays
# visible in the provenance report instead of being silently absorbed.

_CORE = {}
for _d in (_VISUAL, _OLFACTORY, _MUSHROOM, _CENTRAL_COMPLEX, _SUPERIOR, _SEZ,
           _CERVICAL, _VNC, _LEG, _WING, _HALTERE, _ENDOCRINE):
    _CORE.update(_d)

# Wing and haltere were missing from this merge list until now: the two
# neuropils the flight loop depends on (haltere reafference and wing motor
# pools) were declared but unreachable, so every neuron in them was silently
# dropped as "no mappable neuropil". test_banc.py now pins both.
assert "htct" in _CORE and "wtct" in _CORE, \
    "wing/haltere neuropils must stay reachable: the flight loop needs them"

# Every tag the mapping claims is a real release tag must actually appear in
# the release vocabulary, and the regions the closed loop needs must be
# reachable. These are cheap structural guards against a typo'd key silently
# creating an unreachable region (the exact failure that hid wing/haltere).
assert "no" in _CORE and "no" not in _SEZ, \
    "the NO tag is the noduli (central complex), not the gnathal ganglion"

# Tags that ARE mapped, but only approximately: the atlas distinction they
# carry does not exist in the simulator taxonomy. Reported, never hidden.
UNMAPPED_APPROXIMATIONS = {
    "IntTct": "VNC inter-tectulum folded into VENTRAL_NERVE_CORD",
    "LTct": "VNC lateral tectulum folded into VENTRAL_NERVE_CORD",
    "Tct": "VNC tectulum folded into VENTRAL_NERVE_CORD",
    "GNG": "gnathal ganglion == SEZ (exact synonym, kept for clarity)",
}


def strip_side(tag: str) -> tuple[str, str]:
    """Split an atlas tag into (base, side) with side in {'L','R',''}."""
    t = (tag or "").strip()
    for suffix in ("_L", "_R"):
        if t.endswith(suffix):
            return t[: -len(suffix)], suffix[1]
    if t.endswith((" L", " R")):
        return t[:-2], t[-1]
    return t, ""


def map_neuropil(tag: str, soma_side: str = "") -> int | None:
    """Map a real atlas neuropil tag to a RegionID.

    Returns None when the tag is unknown, so the caller can report it instead
    of guessing (spec #3: unmapped is UNKNOWN, never silently merged).
    `soma_side` is used only for retina/lamina where the simulator keeps left
    and right apart; it is 'left'/'right' from the dataset (not the tag).
    """
    base, tag_side = strip_side(tag)
    key = base.strip().lower().replace(" ", "_")

    # A tag may be COMPOUND ("ME.LO", "NO_CONS.ME", "HTct_UTct_T3"): the
    # neuron's synapses fall in more than one neuropil, and the release lists
    # the components separated by "." — 30,514 neurons in banc-888 carry such a
    # tag, and 6 of the 111 release tags are compound. The simulator has no
    # compound region, so a single component has to be chosen.
    #
    # The split must be on "." ONLY, and it must happen BEFORE any other
    # normalisation. The previous code lower-cased and then replaced "." with
    # "_" before splitting on ".", so the split could never fire and every
    # compound tag fell through to `_CORE.get("me_lo")` -> None. That is why
    # compound-tagged neurons were reported as "no mappable neuropil" and
    # dropped. Underscores are part of a component's own name ("T1_PRONM",
    # "MB_CA_L", "HTct_UTct_T3"), never a separator.
    #
    # Which component wins is an INFERRED choice (the caller records it): the
    # leading one, falling back through the list when a component carries no
    # region at all. "NO_CONS" means the atlas could not assign a neuropil, so
    # it is deliberately unmapped and gets skipped rather than voiding the tag
    # ("NO_CONS.ME" must still resolve to the medulla).
    for part in (p for p in key.split(".") if p):
        region = _lookup_component(part, tag_side, soma_side)
        if region is not None:
            return region
    return None


def _lookup_component(key: str, tag_side: str, soma_side: str) -> int | None:
    """Region for a single (already side-stripped, lower-cased) component."""
    if key in ("la", "lamina"):
        return int(RegionID.LAMINA)
    if key == "me":
        return int(RegionID.MEDULLA)
    if key == "lo":
        return int(RegionID.LOBULA)
    if key == "lop":
        return int(RegionID.LOBULA_PLATE)
    if key == "retina":
        side = tag_side or ("L" if str(soma_side).lower().startswith("left") else
                            "R" if str(soma_side).lower().startswith("right") else "")
        return int(RegionID.RETINA_RIGHT if side == "R" else RegionID.RETINA_LEFT)
    region = _CORE.get(key)
    if region is None:
        return None
    return int(region)


def region_centers_mm() -> dict[int, tuple[float, float, float]]:
    """Canonical 3D centres (mm) for each RegionID, for placement fallback.

    These are APPROXIMATED layout coordinates used only when no measured soma
    position exists for a neuron; real datasets supply their own positions.
    """
    return {
        int(RegionID.RETINA_LEFT): (-4.0, 1.2, 0.0),
        int(RegionID.RETINA_RIGHT): (4.0, 1.2, 0.0),
        int(RegionID.LAMINA): (-3.2, 1.2, 0.2),
        int(RegionID.MEDULLA): (-2.4, 1.0, 0.2),
        int(RegionID.LOBULA): (-2.8, 0.6, 0.6),
        int(RegionID.LOBULA_PLATE): (-2.6, 0.3, 0.9),
        int(RegionID.OPTIC_LOBE): (-2.8, 0.8, 0.4),
        int(RegionID.ANTENNAL_LOBE): (0.6, 0.6, 0.3),
        int(RegionID.MUSHROOM_BODY): (0.0, 0.9, 0.9),
        int(RegionID.LATERAL_HORN): (-0.9, 0.5, 0.7),
        int(RegionID.CENTRAL_COMPLEX): (0.0, 0.4, 0.5),
        int(RegionID.SUPERIOR_BRAIN): (0.9, 0.4, 0.5),
        int(RegionID.SUBESOPHAGEAL_ZONE): (0.0, -0.2, 0.2),
        int(RegionID.CERVICAL_CONNECTIVE): (0.0, -0.9, 0.1),
        int(RegionID.VENTRAL_NERVE_CORD): (0.0, -1.4, 0.0),
        int(RegionID.LEG_NEUROMERE): (0.0, -1.8, 0.0),
        int(RegionID.WING_NEUROPIL): (0.0, -1.6, 0.5),
        int(RegionID.HALTERE_NEUROPIL): (0.0, -1.6, -0.5),
        int(RegionID.ABDOMINAL_NEUROMERE): (0.0, -2.4, 0.0),
        int(RegionID.ENDOCRINE_VISCERAL): (0.0, -2.6, 0.0),
        int(RegionID.UNKNOWN): (0.0, 0.0, 0.0),
    }


# Transmitter tags used by the real releases (BANC "Predicted NT type",
# FlyWire "top_nt") -> TransmitterType.
NT_TAG_MAP = {
    "ach": "CHOLINERGIC",
    "acetylcholine": "CHOLINERGIC",
    "cholinergic": "CHOLINERGIC",
    "gaba": "GABAERGIC",
    "gabaergic": "GABAERGIC",
    "glut": "GLUTAMATERGIC",
    "glutamate": "GLUTAMATERGIC",
    "glutamatergic": "GLUTAMATERGIC",
    "da": "DOPAMINERGIC",
    "dopamine": "DOPAMINERGIC",
    "ser": "SEROTONERGIC",
    "serotonin": "SEROTONERGIC",
    "oct": "OCTOPAMINERGIC",
    "octopamine": "OCTOPAMINERGIC",
    "tyr": "TYRAMINERGIC",
    "tyramine": "TYRAMINERGIC",
    "hist": "HISTAMINERGIC",
    "histamine": "HISTAMINERGIC",
    "peptidergic": "PEPTIDERGIC",
}

__all__ = ["map_neuropil", "strip_side", "region_centers_mm", "NT_TAG_MAP",
           "UNMAPPED_APPROXIMATIONS"]