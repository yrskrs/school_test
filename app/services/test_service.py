import json
import random
from typing import List, Optional

from sqlalchemy.orm import Session

from app import crud, models


from collections import defaultdict

def get_attempt_questions(db: Session, attempt: models.StudentAttempt) -> List[models.Question]:
    existing_answers = crud.get_answers_by_attempt(db, attempt.id)
    if existing_answers:
        questions = [ans.question for ans in existing_answers]
        if attempt.session.test.shuffle_questions:
            random.shuffle(questions)
        return questions

    test = attempt.session.test
    
    # Filter out questions from excluded topics
    excluded = []
    if test.excluded_topics:
        try:
            excluded = json.loads(test.excluded_topics)
        except Exception:
            pass
            
    all_questions = [q for q in test.questions if (q.topic or "") not in excluded]
    limit = test.random_questions_limit
    
    if limit and 0 < limit < len(all_questions):
        group_map = defaultdict(list)
        for q in all_questions:
            q_type_str = q.question_type.value if hasattr(q.question_type, 'value') else q.question_type
            group_key = (q.topic or "", q_type_str)
            group_map[group_key].append(q)
            
        group_keys = list(group_map.keys())
        for k in group_keys:
            random.shuffle(group_map[k])
            
        targets = {k: 0 for k in group_keys}
        remaining = limit
        
        active_groups = set(group_keys)
        while remaining > 0 and active_groups:
            keys = list(active_groups)
            random.shuffle(keys)
            
            for k in keys:
                if remaining == 0:
                    break
                if targets[k] < len(group_map[k]):
                    targets[k] += 1
                    remaining -= 1
                else:
                    active_groups.remove(k)
                    
        selected_questions = []
        for k in group_keys:
            selected_questions.extend(group_map[k][:targets[k]])
            
        questions = selected_questions
    else:
        questions = all_questions
        
    for q in questions:
        ans = models.StudentAnswer(
            attempt_id=attempt.id,
            question_id=q.id,
            answer_text=None,
            selected_options_json=None,
            is_correct=None,
            awarded_points=0.0
        )
        db.add(ans)
    db.commit()
    
    if test.shuffle_questions:
        random.shuffle(questions)
    return questions


def get_shuffled_options(question: models.Question, shuffle: bool) -> List[models.AnswerOption]:
    options = list(question.options)
    if shuffle:
        random.shuffle(options)
    return options


def build_test_payload(db: Session, attempt: models.StudentAttempt, shuffle: bool = False) -> dict:
    """
    Будує словник тесту для передачі у шаблон.
    Перемішує питання та варіанти за налаштуваннями тесту.
    """
    test = attempt.session.test
    questions = get_attempt_questions(db, attempt)
    result_questions = []
    for q in questions:
        options = get_shuffled_options(q, shuffle and test.shuffle_options)
        result_questions.append({
            "id": q.id,
            "question_text": q.question_text,
            "question_type": q.question_type.value,
            "points": q.points,
            "topic": q.topic,
            "order_index": q.order_index,
            "image_url": q.image_url,
            "options": [
                {
                    "id": o.id, 
                    "option_text": o.option_text, 
                    "order_index": o.order_index,
                    "image_url": o.image_url,
                    "matching_text": o.matching_text if q.question_type == models.QuestionType.matching else None
                }
                for o in options
            ],
        })
    return {
        "id": test.id,
        "title": test.title,
        "subject": test.subject,
        "class_name": test.class_name,
        "description": test.description,
        "time_limit_minutes": test.time_limit_minutes,
        "time_limit_per_question": test.time_limit_per_question,
        "show_result_after_finish": test.show_result_after_finish,
        "questions": result_questions,
        "question_count": len(result_questions),
    }


def count_test_max_score(test: models.Test) -> float:
    return sum(q.points for q in test.questions)


def validate_test_for_launch(test: models.Test) -> Optional[str]:
    """Повертає рядок з помилкою або None якщо тест готовий до запуску."""
    if not test.questions:
        return "Тест не має жодного питання"
    for q in test.questions:
        if q.question_type in (
            models.QuestionType.single_choice,
            models.QuestionType.multiple_choice,
            models.QuestionType.true_false,
        ):
            if not q.options:
                return f"Питання «{q.question_text[:40]}» не має варіантів відповіді"
            if not any(o.is_correct for o in q.options):
                return f"Питання «{q.question_text[:40]}» не має правильного варіанту"
    return None
