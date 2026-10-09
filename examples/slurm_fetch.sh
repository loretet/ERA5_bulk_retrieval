#!/bin/bash
# Collect an ERA5 retrieval on a cluster, with the data on scratch space.
#
# 1. From anywhere (laptop or cluster) send the requests:   era5-bulk submit my_data.toml
#    If you sent them from your laptop, copy <data_dir>/cds_requests.json to the same place under
#    $ERA5_BULK_DATA_DIR below before running this script.
# 2. sbatch examples/slurm_fetch.sh
#
# Compute nodes often have NO internet access. If the job cannot reach the CDS, run `era5-bulk run --once` from
# a login node instead, with cron (see crontab.txt): it only needs a moment of CPU per run.

#SBATCH --job-name=era5-bulk
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --output=era5-bulk-%j.log

module load cdo 2>/dev/null || true          # or: conda activate era5
export ERA5_BULK_DATA_DIR=/scratch/$USER/ERA5_data

# Keep collecting for the whole allocation, looking every 10 minutes. Safe to resubmit when it ends:
# nothing is ever downloaded or requested twice.
era5-bulk run "$HOME/my_data.toml" --wait-minutes 10
