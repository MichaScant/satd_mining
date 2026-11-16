==============================================================================
RQ1 — Do coding agents DOCUMENT the technical debt they introduce, and how often?
        (PREDICTION / projected values — split by agent)
==============================================================================
metric                                                   devin      claude
--------------------------------------------------------------------------
documentation rate — in code (% commits)                  1.9%        3.2%
documentation rate — overall (% of PRs)                    26%        100%
  of which on its OWN (spontaneous)                         2%          3%
  of which developer-elicited                              25%         97%
where documented: code comments                            55%         23%
where documented: PR discussion                            45%         77%
debt type: design                                          94%         95%
debt type: bug                                              3%          3%
debt type: refactoring                                      2%          3%


==============================================================================
RQ2 — How often do agents REVISIT the technical debt they introduced?
        (PREDICTION / projected values — split by agent)
==============================================================================
metric                                                   devin      claude
--------------------------------------------------------------------------
revisits the file before resolution (% of debt)            14%         20%
  of which edits NEAR the SATD (% of debt)                  6%         12%
mean AI file-edits per debt                               0.34        1.76
of SOLVED debt — 0 edits before fix                        96%         82%
                 1 edit                                     4%         18%
                 2+ edits                                   0%          0%
convo revisit — developer-elicited                         90%         89%
convo revisit — spontaneous                                10%         11%

(PREDICTION values use the projection model: small-sample agents — esp. claude — are regularised toward the pooled distribution; see proj_adm_* graphs. Not real measurements.)