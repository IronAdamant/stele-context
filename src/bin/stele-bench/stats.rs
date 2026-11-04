//! D13 regression statistics.
//!
//! A candidate is slower only when both checks say so: the 95% confidence
//! intervals of the two medians do not overlap and the candidate sits
//! entirely above the baseline, and a one-sided Mann-Whitney test says the
//! candidate is stochastically larger at p < 0.05. Either test alone is not
//! a failure. Both need at least 15 runs. A tie at exactly p = 0.05 is not
//! a failure.

pub(crate) const MIN_RUNS: usize = 15;
const ALPHA: f64 = 0.05;

#[derive(Clone, Debug)]
pub(crate) struct Verdict {
    pub baseline_median: f64,
    pub candidate_median: f64,
    pub baseline_ci: (f64, f64),
    pub candidate_ci: (f64, f64),
    pub ci_slower: bool,
    pub mannwhitney_p: f64,
    pub mw_slower: bool,
    pub reject: bool,
}

pub(crate) fn require_runs(runs: usize, allow_short: bool) -> Result<(), String> {
    if runs < 1 || (runs < MIN_RUNS && !allow_short) {
        return Err(format!(
            "D13 requires at least {MIN_RUNS} runs; got {runs}. \
             --allow-short is for a local smoke test, not CI."
        ));
    }
    Ok(())
}

pub(crate) fn median(values: &[f64]) -> Result<f64, String> {
    if values.is_empty() {
        return Err("median of an empty sample".to_string());
    }
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let mid = ordered.len() / 2;
    if ordered.len() % 2 == 1 {
        Ok(ordered[mid])
    } else {
        Ok((ordered[mid - 1] + ordered[mid]) / 2.0)
    }
}

pub(crate) fn median_interval_rank(n: usize) -> Result<usize, String> {
    if n < 1 {
        return Err("empty sample".to_string());
    }
    if n == 1 {
        return Ok(1);
    }
    let tail = ALPHA / 2.0;
    let scale = 2.0_f64.powi(n as i32);
    let mut acc = 0.0;
    let mut rank = 1usize;
    for i in 0..n {
        acc += binom(n, i);
        if acc / scale <= tail {
            rank = i + 1;
        } else {
            break;
        }
    }
    Ok(rank.min((n + 1) / 2))
}

pub(crate) fn median_ci(values: &[f64]) -> Result<(f64, f64), String> {
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let n = ordered.len();
    let rank = median_interval_rank(n)?;
    Ok((ordered[rank - 1], ordered[n - rank]))
}

pub(crate) fn beats_floor(candidate_median: f64, python_median: f64) -> bool {
    candidate_median < python_median
}

pub(crate) fn regression(baseline: &[f64], candidate: &[f64]) -> Result<Verdict, String> {
    if baseline.len() < MIN_RUNS || candidate.len() < MIN_RUNS {
        return Err(format!(
            "D13 requires at least {MIN_RUNS} runs on each side"
        ));
    }
    let (base_lo, base_hi) = median_ci(baseline)?;
    let (cand_lo, cand_hi) = median_ci(candidate)?;
    let ci_slower = cand_lo > base_hi;
    let p_value = mannwhitney_greater(candidate, baseline)?;
    let mw_slower = p_value < ALPHA;
    Ok(Verdict {
        baseline_median: median(baseline)?,
        candidate_median: median(candidate)?,
        baseline_ci: (base_lo, base_hi),
        candidate_ci: (cand_lo, cand_hi),
        ci_slower,
        mannwhitney_p: p_value,
        mw_slower,
        reject: ci_slower && mw_slower,
    })
}

fn mannwhitney_greater(candidate: &[f64], baseline: &[f64]) -> Result<f64, String> {
    let n = candidate.len();
    let m = baseline.len();
    if n == 0 || m == 0 {
        return Err("empty sample".to_string());
    }
    let mut combined = Vec::with_capacity(n + m);
    combined.extend_from_slice(candidate);
    combined.extend_from_slice(baseline);
    let ranks = average_ranks(&combined);
    let rank_sum: f64 = ranks[..n].iter().sum();
    let u = rank_sum - n as f64 * (n as f64 + 1.0) / 2.0;
    let ties = tie_sizes(&combined);
    if ties.is_empty() && n <= 20 && m <= 20 {
        let ways = pairwise_frequencies(n, m)?;
        let u_obs = u.round() as usize;
        let total = binom_u64(n + m, n);
        let numer: u64 = ways.iter().skip(u_obs).sum();
        return Ok(numer as f64 / total as f64);
    }
    let capital_n = n + m;
    let tie_sum: f64 = ties
        .iter()
        .map(|t| {
            let t = *t as f64;
            t * t * t - t
        })
        .sum();
    let mut var = n as f64 * m as f64 * (capital_n as f64 + 1.0) / 12.0;
    if capital_n > 1 {
        var -= n as f64 * m as f64 * tie_sum / (12.0 * capital_n as f64 * (capital_n as f64 - 1.0));
    }
    if var <= 0.0 {
        return Ok(1.0);
    }
    let z = (u - n as f64 * m as f64 / 2.0 - 0.5) / var.sqrt();
    Ok(0.5 * erfc(z / 2.0_f64.sqrt()))
}

/// Abramowitz and Stegun 7.1.26. Absolute error is below 1.5e-7.
/// `f64::erfc` is still unstable, and this crate takes no dependencies.
fn erfc(x: f64) -> f64 {
    if x < 0.0 {
        return 2.0 - erfc(-x);
    }
    const P: f64 = 0.3275911;
    const A1: f64 = 0.254829592;
    const A2: f64 = -0.284496736;
    const A3: f64 = 1.421413741;
    const A4: f64 = -1.453152027;
    const A5: f64 = 1.061405429;
    let t = 1.0 / (1.0 + P * x);
    let poly = t * (A1 + t * (A2 + t * (A3 + t * (A4 + t * A5))));
    poly * (-x * x).exp()
}

fn tie_sizes(values: &[f64]) -> Vec<usize> {
    let mut ordered = values.to_vec();
    ordered.sort_by(f64::total_cmp);
    let mut ties = Vec::new();
    let mut index = 0;
    while index < ordered.len() {
        let mut end = index;
        while end + 1 < ordered.len() && ordered[end + 1] == ordered[index] {
            end += 1;
        }
        let count = end - index + 1;
        if count > 1 {
            ties.push(count);
        }
        index = end + 1;
    }
    ties
}

fn average_ranks(values: &[f64]) -> Vec<f64> {
    let mut order: Vec<usize> = (0..values.len()).collect();
    order.sort_by(|&left, &right| values[left].total_cmp(&values[right]));
    let mut ranks = vec![0.0; values.len()];
    let mut index = 0;
    while index < order.len() {
        let mut end = index;
        while end + 1 < order.len() && values[order[end + 1]] == values[order[index]] {
            end += 1;
        }
        let avg = (index + 1 + end + 1) as f64 / 2.0;
        for slot in order.iter().take(end + 1).skip(index) {
            ranks[*slot] = avg;
        }
        index = end + 1;
    }
    ranks
}

fn pairwise_frequencies(n: usize, m: usize) -> Result<Vec<u64>, String> {
    let mut prev = vec![vec![0_u64; 1]; m + 1];
    for row in prev.iter_mut() {
        row[0] = 1;
    }
    for i in 1..=n {
        let mut cur = vec![vec![0_u64; i * m + 1]; m + 1];
        cur[0][0] = 1;
        for j in 1..=m {
            let add = j;
            let left = cur[j - 1].clone();
            let earlier = prev[j].clone();
            for u in 0..=(i * j) {
                let from_left = left.get(u).copied().unwrap_or(0);
                let from_prev = if u >= add {
                    earlier.get(u - add).copied().unwrap_or(0)
                } else {
                    0
                };
                cur[j][u] = from_left + from_prev;
            }
        }
        prev = cur;
    }
    prev.into_iter()
        .nth(m)
        .ok_or_else(|| "mann-whitney table missing".to_string())
}

fn binom(n: usize, k: usize) -> f64 {
    binom_u64(n, k) as f64
}

fn binom_u64(n: usize, k: usize) -> u64 {
    if k > n {
        return 0;
    }
    let k = k.min(n - k);
    let mut acc = 1_u64;
    for i in 0..k {
        acc = acc * (n - i) as u64 / (i as u64 + 1);
    }
    acc
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn median_odd_and_even() {
        assert_eq!(median(&[3.0, 1.0, 2.0]).unwrap(), 2.0);
        assert_eq!(median(&[1.0, 2.0, 3.0, 4.0]).unwrap(), 2.5);
    }

    #[test]
    fn median_interval_for_fifteen_runs() {
        assert_eq!(median_interval_rank(15).unwrap(), 4);
        let sample: Vec<f64> = (0..15).map(|value| value as f64).collect();
        assert_eq!(median_ci(&sample).unwrap(), (3.0, 11.0));
        let covered = (4..12).map(|k| binom(15, k)).sum::<f64>() / 2.0_f64.powi(15);
        assert!(covered >= 0.95, "{covered}");
    }

    #[test]
    fn mannwhitney_frequencies_match_enumeration() {
        let ways = pairwise_frequencies(3, 3).unwrap();
        let mut counts = vec![0_u64; 10];
        for mask in 0..(1_u32 << 6) {
            let chosen: Vec<usize> = (0..6).filter(|bit| mask & (1 << bit) != 0).collect();
            if chosen.len() != 3 {
                continue;
            }
            let other: Vec<usize> = (0..6).filter(|value| !chosen.contains(value)).collect();
            let wins = chosen
                .iter()
                .map(|left| other.iter().filter(|right| left > right).count())
                .sum::<usize>();
            counts[wins] += 1;
        }
        assert_eq!(counts, ways);
        assert_eq!(ways.iter().sum::<u64>(), binom_u64(6, 3));
    }

    #[test]
    fn complete_separation_is_the_boundary() {
        let p = mannwhitney_greater(&[4.0, 5.0, 6.0], &[1.0, 2.0, 3.0]).unwrap();
        assert!((p - 0.05).abs() < 1e-12, "{p}");
    }

    #[test]
    fn identical_samples_are_not_a_regression() {
        let sample: Vec<f64> = (0..MIN_RUNS).map(|value| value as f64).collect();
        let verdict = regression(&sample, &sample).unwrap();
        assert!(!verdict.reject);
    }

    #[test]
    fn large_shift_is_a_regression() {
        let verdict = regression(&[1.0; MIN_RUNS], &[2.0; MIN_RUNS]).unwrap();
        assert!(verdict.ci_slower);
        assert!(verdict.mw_slower);
        assert!(verdict.reject);
    }

    #[test]
    fn mannwhitney_inside_overlapping_intervals_is_not_a_regression() {
        let baseline: Vec<f64> = (0..MIN_RUNS).map(|value| value as f64).collect();
        let candidate: Vec<f64> = (0..MIN_RUNS).map(|value| value as f64 + 3.0).collect();
        let verdict = regression(&baseline, &candidate).unwrap();
        assert!(verdict.mw_slower);
        assert!(!verdict.ci_slower);
        assert!(!verdict.reject);
    }

    #[test]
    fn gate_refuses_short_samples() {
        let err = regression(&[1.0; 14], &[1.0; MIN_RUNS]).unwrap_err();
        assert!(err.contains("15"));
    }

    #[test]
    fn floor_is_strict() {
        assert!(beats_floor(10.0, 11.0));
        assert!(!beats_floor(11.0, 11.0));
    }

    #[test]
    fn runner_refuses_fewer_than_fifteen_runs() {
        assert!(require_runs(14, false).is_err());
        assert!(require_runs(2, true).is_ok());
        assert!(require_runs(15, false).is_ok());
        assert!(require_runs(0, true).is_err());
    }
}
