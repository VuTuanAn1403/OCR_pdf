from benchmark.scientific_evaluation.reading_order_v6_2 import evaluate_reading_order


def nodes():
    return [{"id": f"r{i}", "text": text, "bbox": [x, 0, x + 10, 10]}
            for i, (x, text) in enumerate(((0, "1"), (20, "1"), (40, "A")))]


def test_short_and_duplicate_text_is_aligned_by_geometry():
    ref = nodes()
    report = evaluate_reading_order(ref, [ref[1], ref[0], ref[2]])
    assert report["coverage"] == 1
    assert report["precedence_accuracy"] == 2/3
    assert not report["exact_order_page"]


def test_missing_and_duplicate_predictions_are_visible():
    ref = nodes()
    report = evaluate_reading_order(ref, [ref[0], ref[0], ref[2]])
    assert report["coverage"] == 2/3
    assert report["precision"] == 2/3
    assert report["duplicate_predictions"] == 1
    assert report["penalized_order_score"] == 2/3
    assert evaluate_reading_order(ref, [])["penalized_order_score"] == 0
    assert evaluate_reading_order(ref, [ref[0]])["precedence_accuracy"] is None


def test_partial_order_does_not_invent_article_order():
    ref = nodes()
    report = evaluate_reading_order(ref, [ref[2], ref[0], ref[1]], before_pairs=[("r0", "r1")])
    assert report["precedence_accuracy"] == 1
    assert report["annotated_pair_fraction"] == 1/3
    assert report["exact_order_page"] is None
    assert evaluate_reading_order([], [])["status"] == "NOT_EVALUATED_EMPTY_GT"
