pub(super) fn cosine(left: &[f64], right: &[f64]) -> f64 {
    if left.is_empty() || left.len() != right.len() {
        return -1.0;
    }
    let numerator = left
        .iter()
        .zip(right)
        .map(|(left, right)| left * right)
        .sum::<f64>();
    let left_norm = left.iter().map(|value| value * value).sum::<f64>().sqrt();
    let right_norm = right.iter().map(|value| value * value).sum::<f64>().sqrt();
    if left_norm <= f64::EPSILON || right_norm <= f64::EPSILON {
        -1.0
    } else {
        numerator / (left_norm * right_norm)
    }
}

pub(super) fn mean_vector(vectors: &[Vec<f64>]) -> Vec<f64> {
    let Some(width) = vectors.first().map(Vec::len).filter(|width| *width > 0) else {
        return Vec::new();
    };
    let valid = vectors
        .iter()
        .filter(|vector| vector.len() == width)
        .collect::<Vec<_>>();
    if valid.is_empty() {
        return Vec::new();
    }
    let mut totals = vec![0.0; width];
    for vector in &valid {
        for (total, value) in totals.iter_mut().zip(vector.iter()) {
            *total += value;
        }
    }
    let count = valid.len() as f64;
    totals.into_iter().map(|total| total / count).collect()
}
