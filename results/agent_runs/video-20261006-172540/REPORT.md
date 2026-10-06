Batch: all · 27 job runs · 126 model calls · models returned: glm-5.3

## Total tokens per job — median [min–max] (n)

| Job | stack_a_mcp | stack_c_mcp | privos_skill |
|---|---|---|---|
| lead_intake | 133,550 [100,582–133,865] (3) | 139,690 [104,273–141,010] (3) | 34,757 [34,710–35,648] (3) |
| pipeline_sync | 230,679 [230,037–230,798] (3) | 274,390 [173,974–275,139] (3) | 46,143 [45,109–55,694] (3) |
| daily_digest | 167,959 [167,714–168,492] (3) | 176,003 [139,886–176,704] (3) | 46,645 [37,094–49,267] (3) |

## Sum of per-job medians

| Stack | total | uncached in | cached in | output | fixed prompt/job | calls | weighted c=0.1 | weighted c=0.2 | weighted c=0.5 | est. $ |
|---|---|---|---|---|---|---|---|---|---|---|
| stack_a_mcp | 532,188 | 11,581 | 518,144 | 2,643 | 31,300 | 16 | 73,967 | 125,782 | 281,225 | 0.1626 |
| stack_c_mcp | 590,083 | 15,332 | 540,032 | 3,410 | 32,016 | 17 | 82,975 | 136,978 | 298,988 | 0.1769 |
| privos_skill | 127,545 | 12,601 | 110,848 | 4,267 | 9,056 | 11 | 40,754 | 51,839 | 85,093 | 0.0652 |

## Ratios vs `privos_skill` (sum of per-job medians)

| Stack | total | uncached input | weighted c=0.1 | weighted c=0.2 | weighted c=0.5 | USD |
|---|---|---|---|---|---|---|
| stack_a_mcp | 4.17× | 0.92× | 1.81× | 2.43× | 3.30× | 2.49× |
| stack_c_mcp | 4.63× | 1.22× | 2.04× | 2.64× | 3.51× | 2.71× |

Weights: uncached input 1, output 4.0, cached input c. Set PRICE_INPUT/PRICE_CACHED/PRICE_OUTPUT (USD per 1M) for dollars.
