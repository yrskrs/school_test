import json
import re

from sqlalchemy.orm import Session

from app import crud, models


def normalize_text(text: str) -> str:
    """Нормалізує рядок для порівняння: нижній регістр, видаляє пунктуацію та зайві пробіли."""
    if not text:
        return ""
    text = text.lower()
    # Remove common punctuation and symbols
    text = re.sub(r"[.,\/#!$%\^&\*;:{}=\-_`~()\"\'«»„“’]", "", text)
    return re.sub(r"\s+", " ", text.strip())

def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def fuzzy_match(given: str, expected: str) -> bool:
    if given == expected:
        return True
    
    max_typos = 1 if len(expected) <= 5 else 2
    
    if levenshtein_distance(given, expected) <= max_typos:
        return True
        
    given_words = given.split()
    expected_words = expected.split()
    
    if len(given_words) == len(expected_words) and len(expected_words) > 1:
        given_sorted = "".join(sorted(given_words))
        expected_sorted = "".join(sorted(expected_words))
        if levenshtein_distance(given_sorted, expected_sorted) <= max_typos:
            return True
            
    return False

def is_point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Перевіряє, чи знаходиться точка всередині полігону (Ray-casting алгоритм)."""
    n = len(polygon)
    inside = False
    if n == 0:
        return False
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def parse_polygon(region_str: str) -> list[tuple[float, float]]:
    parts = region_str.split('-')
    polygon = []
    for p in parts:
        p = p.strip().strip('()')
        if ',' in p:
            try:
                px, py = p.split(',')
                polygon.append((float(px.strip()), float(py.strip())))
            except ValueError:
                continue
    return polygon


def grade_answer(
    question: models.Question,
    answer_text: str | None,
    selected_options_json: str | None,
) -> tuple[bool, float]:
    """
    Оцінює одну відповідь.
    Повертає (is_correct: bool, awarded_points: float).
    """
    q_type = question.question_type

    if q_type == models.QuestionType.short_text:
        if not answer_text:
            return False, 0.0
        correct_options = [o for o in question.options if o.is_correct]
        if correct_options:
            expected = normalize_text(correct_options[0].option_text)
        else:
            return False, 0.0
        given = normalize_text(answer_text)
        
        if getattr(question.test, "use_fuzzy_matching", False):
            is_correct = fuzzy_match(given, expected)
        else:
            is_correct = given == expected
            
        return is_correct, question.points if is_correct else 0.0

    if q_type in (models.QuestionType.single_choice, models.QuestionType.true_false, models.QuestionType.image_choice):
        if not selected_options_json:
            return False, 0.0
        try:
            selected_ids = json.loads(selected_options_json)
        except (json.JSONDecodeError, TypeError):
            return False, 0.0
        if len(selected_ids) != 1:
            return False, 0.0
        correct_ids = {o.id for o in question.options if o.is_correct}
        is_correct = set(selected_ids) == correct_ids
        return is_correct, question.points if is_correct else 0.0

    if q_type == models.QuestionType.multiple_choice:
        if not selected_options_json:
            return False, 0.0
        try:
            selected_ids = set(json.loads(selected_options_json))
        except (json.JSONDecodeError, TypeError):
            return False, 0.0
        correct_ids = {o.id for o in question.options if o.is_correct}
        if not correct_ids:
            return False, 0.0
        is_correct = selected_ids == correct_ids
        return is_correct, question.points if is_correct else 0.0

    if q_type == models.QuestionType.matching:
        if not selected_options_json:
            return False, 0.0
        try:
            submitted_pairs = json.loads(selected_options_json) # dict: { "option_id": "matching_text" }
            if not isinstance(submitted_pairs, dict):
                return False, 0.0
        except (json.JSONDecodeError, TypeError):
            return False, 0.0
            
        correct_count = 0
        total_pairs = len(question.options)
        if total_pairs == 0:
            return False, 0.0
            
        for opt in question.options:
            submitted_match = submitted_pairs.get(str(opt.id))
            # Порівнюємо після нормалізації, щоб не було проблем з пробілами
            if submitted_match and normalize_text(submitted_match) == normalize_text(opt.matching_text or ""):
                correct_count += 1
                
        is_correct = correct_count == total_pairs
        
        if is_correct:
            return True, question.points
        elif question.test.allow_partial_grading and correct_count > 0:
            awarded = (correct_count / total_pairs) * question.points
            return False, round(awarded, 2)
        else:
            return False, 0.0

    if q_type == models.QuestionType.sequence:
        if not selected_options_json:
            return False, 0.0
        try:
            submitted_order = json.loads(selected_options_json) # list of option_ids in submitted order
            if not isinstance(submitted_order, list):
                return False, 0.0
        except (json.JSONDecodeError, TypeError):
            return False, 0.0
            
        # Правильний порядок за order_index
        correct_order = [o.id for o in sorted(question.options, key=lambda x: x.order_index)]
        
        try:
            submitted_ids = [int(x) for x in submitted_order]
        except (ValueError, TypeError):
            return False, 0.0
            
        is_correct = submitted_ids == correct_order
        return is_correct, question.points if is_correct else 0.0

    if q_type == models.QuestionType.hotspot:
        if not selected_options_json:
            return False, 0.0
        try:
            click_data = json.loads(selected_options_json) # dict: {"x": float, "y": float}
            if not isinstance(click_data, dict) or "x" not in click_data or "y" not in click_data:
                return False, 0.0
        except (json.JSONDecodeError, TypeError):
            return False, 0.0
        
        click_x, click_y = click_data["x"], click_data["y"]
        # For hotspot, there is usually 1 correct option containing the region text
        correct_options = [o for o in question.options if o.is_correct]
        
        is_correct = False
        for opt in correct_options:
            polygon = parse_polygon(opt.option_text)
            if is_point_in_polygon(click_x, click_y, polygon):
                is_correct = True
                break
        
        return is_correct, question.points if is_correct else 0.0

    # Невідомий тип — не оцінюємо
    return False, 0.0


def grade_all_answers(
    db: Session,
    attempt: models.StudentAttempt,
) -> tuple[float, float]:
    """
    Оцінює всі відповіді спроби.
    Повертає (total_score, max_score).
    """
    test = attempt.session.test
    answers = crud.get_answers_by_attempt(db, attempt.id)

    total_score = 0.0
    max_score = 0.0

    for answer in answers:
        question = answer.question
        max_score += question.points
        is_correct, awarded = grade_answer(
            question,
            answer.answer_text,
            answer.selected_options_json,
        )
        crud.save_graded_answer(db, answer, is_correct, awarded)
        total_score += awarded

    return total_score, max_score
