"""The complete neuropil vocabulary of the banc-888 release.

Pinned from the real release (111 distinct tags across 158,262 neurons) so the
mapping is exercised WITHOUT the 14 MB pickle — CI has no network and does not
ship the raw data. This is the guard that would have caught the compound-tag
bug: `map_neuropil` lower-cased and replaced "." with "_" BEFORE splitting on
".", so the split could never fire and every compound tag ("ME.LO", "NO_CONS.ME",
"HTct_UTct_T3_L") fell through to None and its neuron was dropped as "no
mappable neuropil" (6,081 neurons, 3.8% of the CNS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flybrain.neuropil import map_neuropil  # noqa: E402

# every tag the release actually uses, in sorted order
RELEASE_TAGS = [
    'ABDNM_L',
    'ABDNM_R',
    'AL_L',
    'AL_R',
    'AME_L',
    'AME_R',
    'AMMC_L',
    'AMMC_R',
    'ANm',
    'AOTU_L',
    'AOTU_R',
    'ATL_L',
    'ATL_R',
    'AVLP_L',
    'AVLP_R',
    'BU_L',
    'BU_R',
    'CAN_L',
    'CAN_R',
    'CRE_L',
    'CRE_R',
    'EB',
    'EPA_L',
    'EPA_R',
    'FB',
    'FLA_L',
    'FLA_R',
    'GA_L',
    'GA_R',
    'GNG',
    'GOR_L',
    'GOR_R',
    'HTct_UTct_T3_L',
    'HTct_UTct_T3_R',
    'IB_L',
    'IB_R',
    'ICL_L',
    'ICL_R',
    'IPS_L',
    'IPS_R',
    'IntTct',
    'LAL_L',
    'LAL_R',
    'LH_L',
    'LH_R',
    'LOP_L',
    'LOP_R',
    'LO_L',
    'LO_R',
    'LTct',
    'LegNp_T1_L',
    'LegNp_T1_R',
    'LegNp_T2_L',
    'LegNp_T2_R',
    'LegNp_T3_L',
    'LegNp_T3_R',
    'MB_CA_L',
    'MB_CA_R',
    'MB_ML_L',
    'MB_ML_R',
    'MB_PED_L',
    'MB_PED_R',
    'MB_VL_L',
    'MB_VL_R',
    'ME_L',
    'ME_R',
    'NO',
    'NTct_UTct_T1_L',
    'NTct_UTct_T1_R',
    'PB',
    'PLP_L',
    'PLP_R',
    'PRW',
    'PVLP_L',
    'PVLP_R',
    'SAD',
    'SCL_L',
    'SCL_R',
    'SIP_L',
    'SIP_R',
    'SLP_L',
    'SLP_R',
    'SMP_L',
    'SMP_R',
    'SPS_L',
    'SPS_R',
    'Tct',
    'VES_L',
    'VES_R',
    'VNC_AMNp_L',
    'VNC_AMNp_R',
    'VNC_HTct_L',
    'VNC_HTct_R',
    'VNC_NTct_L',
    'VNC_NTct_R',
    'VNC_T1_ProNm_L',
    'VNC_T1_ProNm_R',
    'VNC_T2_MesoNm_L',
    'VNC_T2_MesoNm_R',
    'VNC_T2_mVAC_L',
    'VNC_T2_mVAC_R',
    'VNC_T3_MetaNm_L',
    'VNC_T3_MetaNm_R',
    'VNC_WTct_L',
    'VNC_WTct_R',
    'WED_L',
    'WED_R',
    'WTct_UTct_T2_L',
    'WTct_UTct_T2_R',
    'Xnerve',
    'cervical_connective'
]

# Tags that legitimately have no simulator region. Deliberately EMPTY: on
# banc-888 every one of the 111 release tags maps. If this list ever has to
# grow, that release tag is a mapping hole and the ingest reports it under
# `atlas-tag-unmapped` naming the tag.
KNOWN_UNMAPPED: set[str] = set()


def test_every_release_tag_maps_to_a_region():
    unmapped = [t for t in RELEASE_TAGS if map_neuropil(t, "left") is None]
    assert unmapped == sorted(KNOWN_UNMAPPED), (
        "release tags with no simulator region changed - extend the mapping in "
        "flybrain/neuropil.py (or, if genuinely unfixable, list it as "
        f"KNOWN_UNMAPPED with a reason). Unmapped: {unmapped}")


def test_compound_tags_resolve_through_their_components():
    # A compound tag must resolve via its components, not be treated as a
    # single unknown name. "NO_CONS" means "the atlas could not assign a
    # neuropil", so it is skipped rather than voiding the whole tag.
    assert map_neuropil("ME.LO", "left") is not None
    assert map_neuropil("NO_CONS.ME", "left") == map_neuropil("ME", "left")
    assert map_neuropil("ME.NO_CONS", "left") == map_neuropil("ME", "left")
    assert map_neuropil("ME.LO", "left") == map_neuropil("ME", "left")
    # underscores belong to a component's name; they must never be separators
    assert map_neuropil("HTct_UTct_T3_L", "left") is not None
    assert map_neuropil("WTct_UTct_T2_R", "right") is not None
    assert map_neuropil("NTct_UTct_T1_L", "left") is not None
    assert map_neuropil("MB_CA.MB_PED", "left") is not None


def test_a_tag_is_only_split_on_dots():
    # "VNC_T1_ProNm_L" contains underscores AND a trailing side marker. If a
    # split or a side-strip is too eager it loses the "_L" or shatters "T1".
    assert map_neuropil("VNC_T1_ProNm_L", "left") is not None
    base = map_neuropil("VNC_T1_ProNm", "left")
    assert base is not None
    # the trailing side marker must not change which region it is
    assert map_neuropil("VNC_T1_ProNm_R", "right") == base


def test_unknown_tag_is_reported_not_guessed():
    assert map_neuropil("TOTALLY_UNKNOWN_PILOT_REGION", "left") is None
    # and it must not be silently re-homed by a fuzzy match
    assert map_neuropil("ME_UNKNOWN", "left") is None