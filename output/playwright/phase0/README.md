# Phase 0 browser evidence

Generated on 2026-08-30 with Playwright CLI and Chromium 152 against the local Django SSR application and the actually activated `RECENT_RETAIL` publication.

`scripts/browser_acceptance.js` passed at `360x800`, `390x844`, `768x1024`, and `1440x900`. At every viewport it checked the actual catalog and detail flow, the loading/empty/unavailable/stale/server-error matrix, validation semantics, long Korean identity/source/freshness copy, document width, 44 px interactive targets, one `main`, one `h1`, Korean document language, logical tab order, visible skip-link focus, and text labels in addition to status color. Mobile input, submit, fixed validation response, correction, and resubmission passed at 360 px and 390 px.

Manual screenshot inspection covered mobile catalog, mobile validation, mobile long-name stale detail, tablet catalog/detail, desktop catalog/detail, and desktop server error. No clipping, overlap, horizontal scrolling, hidden status meaning, or unreadable hierarchy was found after the mobile duplicate-breadcrumb fix.

axe-core 4.13.0 checked the actual catalog and detail against WCAG 2 A/AA, WCAG 2.1 A/AA, and WCAG 2.2 AA: both pages had zero violations. Its only incomplete rule was contrast for decorative non-text symbols and text over the current-value gradient. The separate palette test checks every rendered foreground/background pair plus both gradient extremes at 4.5:1 or greater. `axe-results.json` SHA-256 is `a994c5a00a9f5f75213381c5be2ef49624eb796e202d3becd0daef4818d076a6`.

All PNGs are full-page captures at the width encoded in the filename. SHA-256:

```text
06d9871ab7a545ced4be51ffc2ce8d516064c2955c89d171ae83bdc7a4adf8af  1440x900-catalog.png
dbee6272fae59a31f410232b8a4cfe9e12ff67a800a1140dd777c96c748601e1  1440x900-detail.png
48c8696294ce930ab947744d0f81cdb9224d95c5cd1bf3dd0fea8af0c690eb18  1440x900-long-stale-detail.png
6641021c6a454aa3733a9890b6e3bc5d20214229e20e203fa9c127fc4de78311  1440x900-server-error.png
1f5a0391d7e3745d472010f416ed97e9a227fb6989fbd3cfd07ddc2ffd3d2292  360x800-catalog.png
ee778478f65f3dcdb49aeb615b8c91d1bde60931caae8208c680b613d9ab74de  360x800-detail.png
ce072f620bd1c83cd29f5e061518b366f7087c570bce2a03e384cfef1202eece  360x800-loading.png
2c82cd8a8a7d56c51595089827b60f9122945eaa9ffb4b18765c97cbb949b97a  360x800-long-stale-detail.png
19db51bbf71c9bbcdbf08a56a4fd27bc9968c7dc40ef1a086aea4f3de3734ca2  360x800-validation.png
3e7473283a53c123f679f74db5eff126c607f21c992a6227ce9722239fcbe18d  390x844-catalog.png
4cb4307c6b5cb060618c57ca77e10beab88c43e7d05f29ff214de53b267526c4  390x844-detail.png
6a3169fd6aa0c65567fc57b8a3a8dee3b65c8f58da5c3592f16dfd501b74b041  390x844-empty.png
b6f320b4044388d062719ca868a5d799323d60d7dc919062349c91d22ac68e0c  390x844-long-stale-detail.png
7a3445d55cabb094e639050840ac4c37e247f7424f7eeb264d518b8c76161b4d  390x844-validation.png
5b1f9697f737a3015bfb9aa144a6c116eb19936b49b8d8b02556ded2a8075160  768x1024-catalog.png
1c51c9a7a467928f7e567c743695dedddd5b3632f7be8ac2ea051799a8551550  768x1024-detail.png
aa05b22b7ed2ee14c99f50cee263a0cdad01a73d3072dd44557ac44f93a2d766  768x1024-long-stale-detail.png
b5b9a9dfcd92877bb24c73f41112b66e04306100a06e14569358caae95676417  768x1024-unavailable.png
```
