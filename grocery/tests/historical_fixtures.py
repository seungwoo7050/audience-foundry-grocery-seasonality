"""Synthetic reviewed dimensions and rows shared by historical parser tests."""

from grocery.source.historical_dimensions import HistoricalDimensionRegistry
from grocery.source.registry import INITIAL_RETAIL_IDENTITY_REGISTRY


def historical_registry() -> HistoricalDimensionRegistry:
    return HistoricalDimensionRegistry(
        identity_registry=INITIAL_RETAIL_IDENTITY_REGISTRY,
        region_names={"11000": "서울", "26000": "부산"},
        market_names={
            ("11000", "110001"): "합성서울시장",
            ("26000", "260001"): "합성부산시장",
        },
        dimension_evidence_revision="synthetic-reviewed-v1",
    )


def monthly_row() -> dict[str, str]:
    return {
        "exmn_ym": "202602",
        "sgg_cd": "11000",
        "sgg_nm": "서울",
        "se_cd": "01",
        "se_nm": "소매",
        "ctgry_cd": "200",
        "ctgry_nm": "채소류",
        "item_cd": "212",
        "item_nm": "양배추",
        "vrty_cd": "00",
        "vrty_nm": "양배추",
        "grd_cd": "04",
        "grd_nm": "상품",
        "unit": "포기",
        "unit_sz": "1",
        "pmm_avgprc": "1000.50",
        "pmm_hgprc": "1200",
        "pmm_lwprc": "800",
        "pmm_stddvtn": "100.25",
        "pmm_cfcntvrtn": "10.02",
        "pmm_cfcntrng": "400",
        "pyy_avgprc": "900",
        "pyy_hgprc": "1100",
        "pyy_lwprc": "700",
        "pyy_stddvtn": "90",
        "pyy_cfcntvrtn": "10",
        "pyy_cfcntrng": "400",
        "orgnl_reg_dt": "2026-08-31 12:00:00",
    }


def regional_row() -> dict[str, str]:
    return {
        "exmn_ymd": "20260831",
        "se_cd": "01",
        "se_nm": "소매",
        "ctgry_cd": "200",
        "ctgry_nm": "채소류",
        "item_cd": "212",
        "item_nm": "양배추",
        "vrty_cd": "00",
        "vrty_nm": "양배추",
        "grd_cd": "04",
        "grd_nm": "상품",
        "sgg_cd": "11000",
        "sgg_nm": "서울",
        "unit": "포기",
        "unit_sz": "1",
        "exmn_dd_min_prc": "800",
        "exmn_dd_cnvs_min_prc": "80.5",
        "exmn_dd_avg_prc": "1000",
        "exmn_dd_cnvs_avg_prc": "100.25",
        "exmn_dd_max_prc": "1200",
        "exmn_dd_cnvs_max_prc": "120.75",
    }
