# era5-bulk-retrieval

Download ERA5 from the Copernicus Climate Data Store (CDS) **in bulk, without keeping your computer on**,
without hitting the CDS limits, and with a final check that **no hour is missing**.

You describe *what* you want in a small config file (period, levels, variables, and optionally a list of
points). The tool then:

1. **sends all the requests at once** and returns within minutes – the CDS works on them on its own servers, so
   you can switch your computer off;
2. **collects the results whenever they are ready**, one by one, and – if you gave points – **cuts your points
   out of each big file and deletes it**, so a 250 GB retrieval needs only a few GB of disk at any time;
3. **remembers everything** in one small JSON file, so it can be interrupted, restarted, run from a different
   machine, or run once a day by a scheduler, and never downloads or requests anything twice;
4. **validates** the result: every timestamp present, every timestep complete, no truncated file.

```text
$ era5-bulk plan five_sites.toml
Period           2020-01-01/2021-12-31  (731 days -> 48 pieces of at most 16 days)
Area             72.5/-97.5/36.5/5  (145 x 411 = 59,595 grid points)
Requests         48   (CDS queue limit: 150)
Size / request   4.5 - 5.2 GB   (CDS support advises < 20 GB)
Total download   237 GB   (deleted piece by piece: peak disk use ~one request)
Kept             5 point(s), ~2.5 GB each for the whole period
```

> **Status: v0.1.** The logic grew out of scripts used for a real (ongoing) two-year, five-site download of ERA5 model
> levels. This package itself has been tested against a simulated CDS and real CDO (including interrupted
> downloads, lost requests and crashes mid-processing), and its GRIB reader against real ERA5 files – but it has
> not yet been run against the live CDS in this form. Please run the 2-minute
> [smoke test](#try-it-in-two-minutes) before a big retrieval, and open an issue if the live CDS behaves
> differently from what is described here.

---

## Why this exists

Downloading ERA5 *model-level* data for a few locations over a few years runs into the same walls again and
again – they are the recurring themes of CDS forum threads such as "cost limits exceeded" and "best strategy to
retrieve the data":

| Wall | What happens | What this tool does |
|---|---|---|
| **Request too long/large** | `cost limits exceeded` for requests of more than ~15 days of model levels | Cuts the period into pieces of at most `max_days` (default 16), one request each, never crossing a month |
| **Too many queued requests** | The CDS allows a limited number of queued requests per user (150 in Oct 2026) | `plan` warns you; `max_queued` + `run` feed the queue as results are collected |
| **One request per site is slow** | ERA5 is archived on tapes at ECMWF; as explained on the forum, repeatedly asking for the *same tape* (different places, same dates) lowers the priority of your requests. The area is applied *after* the data is read, so a small area costs as much tape reading as a large one | **One area for all your points**, then the points are cut out locally |
| **`cdsapi.retrieve()` blocks** | It waits until the result is ready – hours or days – so the laptop must stay on and connected | Requests are *submitted* without waiting; results are *fetched* later |
| **Disk fills up** | Area files of several GB each, hundreds of them | Each file is processed and deleted right after download |
| **Did I get everything?** | A single failed or expired piece leaves a silent gap | `validate` reports missing hours by day |

### One area, many points: the trade-off

Asking for one box around all your points means MARS reads each tape once per piece (which is what the forum
advice favours), but the box can be much bigger than the sum of the points' neighbourhoods. For the five sites of
[`examples/five_sites.toml`](examples/five_sites.toml) (Europe, Greenland, the Azores, Oklahoma):

| | requests | per request | total transfer |
|---|---|---|---|
| one small box per cluster of nearby sites (3 boxes) | 138 | ≤ 0.5 GB | ≈ 25 GB |
| **one box around everything** (this tool) | **48** | **≈ 5 GB** | **≈ 237 GB** |

*(estimates, see [Size estimates](#size-estimates))*. The single box is 10× more bytes but one third of the
requests and one tape read per piece. If your points are on opposite sides of the globe the box becomes huge:
`plan` warns you, and the better choice is **one retrieval (config file and `data_dir`) per cluster of points**.

---

## Install

```bash
pip install .                       # from a clone of this repository
conda install -c conda-forge cdo    # or: brew install cdo   |   sudo apt install cdo
era5-bulk doctor                    # checks cdsapi, your CDS key and CDO
```

* Python ≥ 3.10. The only Python dependency is `cdsapi` (≥ 0.7.7, the version for the new CDS).
* **CDO** is needed only if you use `points` (to cut them out and merge them). Without points the area files are
  simply kept.
* **CDS account**: create `~/.cdsapirc` with your key
  ([instructions](https://cds.climate.copernicus.eu/how-to-api)) and **accept the licence** of the
  [ERA5 complete](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-complete) dataset on the website
  once, or every request will be rejected.

## Quick start

```bash
era5-bulk init my_data.toml        # writes a commented template
$EDITOR my_data.toml               # period, levels, variables, points
era5-bulk plan my_data.toml        # what will be asked, how big, any warning – sends nothing

era5-bulk submit my_data.toml      # sends every request and returns. You may switch the computer off now.
era5-bulk status my_data.toml      # what is done / queued / still to send (does not contact the CDS)

era5-bulk run my_data.toml         # collects what is ready, sends more, repeats until everything is done
era5-bulk validate my_data.toml    # the final check
```

When everything is downloaded the pieces of each point are merged into `data_dir/merged/<point>.grib`.

### Try it in two minutes

```bash
era5-bulk run examples/smoke_test.toml --data-dir /tmp/era5_smoke
era5-bulk validate examples/smoke_test.toml --data-dir /tmp/era5_smoke
```

One point, two days, one level, one variable: a request of a few kB that shows the whole chain works with your
key. Delete `/tmp/era5_smoke` afterwards. (`run` waits for the CDS, which can take a few minutes even for a tiny
request; press Ctrl-C and run the same command later to continue.)

---

## The configuration file

```toml
data_dir = "~/ERA5_data"
dates    = "2020-01-01/2021-12-31"

levtype  = "ml"                 # "ml" model levels, "sfc" surface, "pl" pressure levels (MARS levtype)
levelist = "110/to/137"         # omit for "sfc"
param    = "130/131/132/133"    # MARS parameter ids: 130 t, 131 u, 132 v, 133 q  (152 lnsp: see FAQ)
time     = "00/to/23/by/1"
grid     = "0.25/0.25"

[points]                        # name = [latitude, longitude]; the nearest grid point is kept
"Cabauw"    = [51.97, 4.93]
"Mace Head" = [53.33, -9.90]
```

| Key | Default | Meaning |
|---|---|---|
| `data_dir` | *(required)* | Everything is stored here. Also settable with `--data-dir` or `$ERA5_BULK_DATA_DIR` (priority: option, variable, file) |
| `dates` | *(required)* | `"YYYY-MM-DD/YYYY-MM-DD"`, both days included |
| `area` | box around the points | `"North/West/South/East"`. Needed if there are no points |
| `points` | none | Points to keep. Without points the whole area files are kept |
| `levtype`, `levelist`, `param`, `time`, `grid` | model levels 110–137, t/u/v/q/lnsp, hourly, 0.25° | The MARS request |
| `type`, `stream`, `format` | `an`, `oper`, `grib` | Analysis, ERA5 stream, GRIB |
| `extra` | none | Any other MARS keyword, e.g. `extra = { "class" = "ea" }` |
| `max_days` | `16` | Longest request. The CDS rejects model-level requests longer than ~15–17 days; lower it for bigger areas |
| `max_queued` | `0` (no limit) | Never keep more than this many requests queued at the CDS |
| `max_attempts` | `3` | Stop re-sending a request that the CDS keeps failing |
| `delete_area_files` | `true` | With points: delete each area file once the points are cut out of it |
| `merge_when_complete` | `true` | Merge each point's pieces into one file at the end |

Unknown keys are an error (so a typo like `levlist` cannot silently do nothing). Complete examples are in
[`examples/`](examples/).

---

## Running unattended

### `--once`: the mode for scheduled jobs

`fetch` and `run` normally **keep checking every `--wait-minutes` until everything is finished** – which can
take days, so the computer must stay on. For a scheduled job use **`--once`**: look once, collect whatever is
ready, send what can be sent, and exit.

```bash
era5-bulk submit my_data.toml          # once, by hand
era5-bulk run    my_data.toml --once   # every few hours, from a scheduler
```

It is safe to run as often as you like: a lock stops two runs from working in the same `data_dir` at the same
time (the second one exits quietly), and a run that finds nothing ready does nothing.
The exit code is `0` normally and `1` if something needs your attention (a download or processing error, or a
request the CDS failed repeatedly).

**macOS (launchd)** – [`examples/launchd.plist`](examples/launchd.plist). launchd does not inherit your shell's
`PATH`, so the plist sets it (otherwise `cdo` is not found). If the Mac is asleep at the scheduled time, the job
runs when it wakes.

**Linux (cron)** – [`examples/crontab.txt`](examples/crontab.txt).

**A cluster / HPC** – [`examples/slurm_fetch.sh`](examples/slurm_fetch.sh). The requests belong to your *CDS
account*, not to a machine, so you can `submit` from your laptop and `run` on the cluster (copy
`data_dir/cds_requests.json`, or just submit from the cluster). Run it where outbound HTTPS works (often the
login node) and point `--data-dir` at scratch space.

### More requests than the CDS lets you queue

If `plan` shows more pieces than the queue limit, set `max_queued` a bit below it and use `run`
(not `submit` alone). Every round it first collects what is ready – freeing queue slots – and then fills them.

> **Collect results within a few days.** The CDS does not keep finished results forever. A request the CDS no
> longer knows is detected and sent again automatically, but that costs you the waiting time.

---

## How it works

### Everything hangs off the file name

Each piece of the period is one job with a deterministic name, e.g. `era5_20200116-20200131.grib`. The list of
jobs is recomputed from the config on every run. The only thing stored is `data_dir/cds_requests.json`:

```json
{
 "signature": "bbc851cc23e3db18",
 "pending":  { "era5_20200116-20200131.grib": "7f3c…" },
 "done":     [ "era5_20200101-20200115.grib" ],
 "attempts": { }
}
```

Which state a job is in is *inferred*, never listed:

| Where the job is | Meaning |
|---|---|
| in `done` | downloaded **and** processed. Counted even though its big file was deleted |
| big file on disk, not in `done` | downloaded, not processed yet (a run was interrupted); processed on the next run, **without downloading again** |
| in `pending` | sent to the CDS, waiting |
| none of the above | still to send |

Details that make it robust:

* The file is rewritten after **every** change, atomically.
* Downloads go to `<name>.part` and are renamed only when complete, so a kill or power cut mid-download can never
  leave something that looks like a finished file.
* A job is marked `done` only **after** its points were extracted – a crash in between is retried, not lost.
* A request the CDS answers "404 / not found" for is sent again; a *network error* is not taken for a lost
  request (that would double-submit), it is just retried next time.
* A request the CDS *fails* is retried up to `max_attempts` times, then reported and left alone, instead of being
  re-sent forever by a scheduled job.
* `signature` is a fingerprint of the request (area, levels, parameters, time, grid – not the dates). A
  `data_dir` that holds requests for something else is refused rather than silently mixed. Extending `dates` is
  fine and only requests the new pieces.

### Files

```text
data_dir/
  cds_requests.json            the state
  area_files/                  big downloaded files, deleted after processing (with points)
  points/<point>/              one small file per piece and point
  merged/<point>.grib          the whole period of a point (made when everything is done)
```

### Python

```python
from era5_bulk import Config, submit, fetch, run, validate, format_plan

cfg = Config(data_dir="~/ERA5_data", dates="2020-01-01/2020-12-31",
             points={"Cabauw": (52.0, 5.0)}, param="130/131/132/133")
print(format_plan(cfg))
submit(cfg)                    # returns at once
fetch(cfg, once=True)          # whenever you like; pass on_file=my_function to do your own processing
for report in validate(cfg): print(report.name, report.ok, report.problems)
```

Treat a `Config` as read-only once created.

---

## Validation

`era5-bulk validate` reads only the GRIB **headers** (about 8 seconds for a two-year, 112-field file of 2 million messages; CDO
takes ~0.65 ms per message, which extrapolates to roughly 20 minutes for the same file) and checks, for each point:

* every expected timestamp of the period is present (otherwise: which days, and how many hours of each);
* every timestep contains **all** the fields the file has elsewhere – an hour with a field missing is reported
  even though its timestamp exists;
* no file is truncated or unreadable;
* a warning if the number of fields per timestep differs from what the request implies.

The exit code is `1` if anything is missing. By default the merged files are checked; `--pieces` checks the
pieces instead. The reader handles GRIB editions 1 and 2 with one field per message, as delivered by the CDS.

## Size estimates

`plan` estimates a request as `grid points × fields × timesteps × 2 bytes` plus ~1.3 kB of header per message
(measured on ERA5 model-level GRIB2). The 2 bytes assume 16-bit packing, which is what the ERA5 fields checked so
far use; compare the first file you download with the estimate before queueing a large retrieval. Even at 32 bits
per value a 15-day, 28-level, 4-variable request over a 145 × 411 box would stay under 10 GB.

---

## FAQ

**`cost limits exceeded` / the request is refused.** The request is too big: lower `max_days`, shrink the area, or
request fewer levels/variables. `era5-bulk plan` shows the size per request before anything is sent.

**Parameter 152 (log of surface pressure) is missing from my files.** It is stored on model level **1 only**.
With `levelist = "110/to/137"` the CDS silently does not return it. Add level 1 to `levelist` (which makes the
request ~5× bigger), or request it in a separate retrieval with `levelist = "1"`; or use surface pressure from a
surface-level request. `plan` and `validate` both warn about this.

**"Your key is of the old CDS".** Create a new key on <https://cds.climate.copernicus.eu> and `pip install -U cdsapi`.

**A point is not exactly where I asked.** ERA5 is on a 0.25° grid; the nearest grid point is used, and `plan`
prints which. Interpolate afterwards if you need the exact location.

**Can I download surface/pressure-level data?** Set `levtype = "sfc"` (no `levelist`) or `"pl"`, with MARS
parameter ids. Only model levels have been exercised so far.

**Windows.** Not tested. The file lock is skipped on Windows, so do not run two instances on the same folder.

## Development

```bash
pip install -e ".[test]"
pytest                    # needs CDO for the extraction/validation tests (they are skipped without it)
```

The tests use a fake CDS client (`tests/fakecds.py`) and small synthetic GRIB files made with CDO.

## Licence

MIT, see [LICENSE](LICENSE). ERA5 data are subject to the
[Copernicus licence](https://cds.climate.copernicus.eu/); please cite them as that licence asks.
