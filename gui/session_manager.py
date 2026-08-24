import os
import sqlite3
from typing import List, Dict, Any

class SessionManager:
    def __init__(self, db_path: str = "data/school_testing.db"):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(root_dir, db_path)
        
    def _get_connection(self):
        if not os.path.exists(self.db_path):
            return None
        return sqlite3.connect(self.db_path)
        
    def get_teachers(self) -> List[Dict[str, Any]]:
        teachers = []
        conn = self._get_connection()
        if not conn: return teachers
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, full_name FROM teachers")
            for row in cursor.fetchall():
                teachers.append({
                    "id": row[0],
                    "username": row[1],
                    "full_name": row[2] or row[1]
                })
        except Exception as e:
            print(f"Помилка отримання вчителів: {e}")
        finally:
            conn.close()
        return teachers
        
    def get_statistics(self, teacher_id: int = None) -> Dict[str, int]:
        stats = {"active_users": 0, "active_tests": 0, "completed_tests": 0, "total_online": 0}
        conn = self._get_connection()
        if not conn: return stats
        try:
            cursor = conn.cursor()
            t_join = "JOIN tests t ON s.test_id = t.id" if teacher_id else ""
            t_cond = f"AND t.teacher_id = {teacher_id}" if teacher_id else ""
            cursor.execute(f"SELECT COUNT(s.id) FROM test_sessions s {t_join} WHERE s.is_active = 1 {t_cond}")
            stats["active_tests"] = cursor.fetchone()[0] or 0
            
            sa_join = "JOIN test_sessions s ON a.session_id = s.id JOIN tests t ON s.test_id = t.id" if teacher_id else ""
            sa_cond = f"AND t.teacher_id = {teacher_id}" if teacher_id else ""
            cursor.execute(f"SELECT COUNT(a.id) FROM student_attempts a {sa_join} WHERE a.status IN ('in_progress', 'paused', 'not_started') {sa_cond}")
            stats["active_users"] = cursor.fetchone()[0] or 0
            
            cursor.execute(f"SELECT COUNT(a.id) FROM student_attempts a {sa_join} WHERE a.status IN ('finished', 'timeout') {sa_cond}")
            stats["completed_tests"] = cursor.fetchone()[0] or 0
            
            cursor.execute(f"SELECT COUNT(a.id) FROM student_attempts a {sa_join} WHERE a.status = 'in_progress' {sa_cond}")
            stats["total_online"] = cursor.fetchone()[0] or 0
        except Exception as e:
            print(f"Помилка отримання статистики: {e}")
        finally:
            conn.close()
        return stats
        
    def get_active_sessions(self, teacher_id: int = None, fetch_answers: bool = True) -> List[Dict[str, Any]]:
        sessions = []
        conn = self._get_connection()
        if not conn: return sessions
        try:
            cursor = conn.cursor()
            t_cond = f"AND t.teacher_id = {teacher_id}" if teacher_id else ""
            query = f"""
                SELECT 
                    a.id as attempt_id,
                    a.student_name,
                    'Невідомо' as ip_address,
                    t.title,
                    a.started_at,
                    a.status,
                    a.finished_at,
                    te.id as teacher_id,
                    te.username as teacher_username,
                    (SELECT COUNT(*) FROM questions q WHERE q.test_id = t.id) as total_questions,
                    t.random_questions_limit
                FROM student_attempts a
                JOIN test_sessions s ON a.session_id = s.id
                JOIN tests t ON s.test_id = t.id
                JOIN teachers te ON t.teacher_id = te.id
                WHERE (s.is_active = 1 AND a.status IN ('in_progress', 'paused', 'not_started'))
                {t_cond}
                ORDER BY a.started_at DESC
                LIMIT 50
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            
            # Fetch answers if needed
            answers_dict = {}
            if fetch_answers and rows:
                attempt_ids = [str(r[0]) for r in rows]
                q_ans = f"SELECT attempt_id, is_correct FROM student_answers WHERE attempt_id IN ({','.join(attempt_ids)}) ORDER BY id ASC"
                cursor.execute(q_ans)
                for a_id, is_correct in cursor.fetchall():
                    if a_id not in answers_dict:
                        answers_dict[a_id] = []
                    answers_dict[a_id].append(is_correct == 1 if is_correct is not None else None)
            
            for row in rows:
                status_map = {
                    'not_started': 'Очікує',
                    'in_progress': 'В процесі',
                    'finished': 'Завершено',
                    'timeout': 'Час вийшов',
                    'paused': 'Призупинено'
                }
                status_ua = status_map.get(row[5], row[5])
                
                tot_cnt = row[9] or 0
                rand_limit = row[10]
                if rand_limit is not None and rand_limit > 0:
                    tot_cnt = min(tot_cnt, rand_limit)
                    
                att_id = row[0]
                ans_list = answers_dict.get(att_id, [])
                
                sessions.append({
                    "attempt_id": att_id,
                    "student_name": row[1],
                    "ip": row[2],
                    "test_name": row[3],
                    "started_at": row[4],
                    "status": status_ua,
                    "raw_status": row[5],
                    "progress_answers": ans_list,
                    "total_questions": tot_cnt,
                    "teacher_id": row[7],
                    "teacher_username": row[8],
                    "last_activity": row[6] or row[4] or "Зараз"
                })
        except Exception as e:
            print(f"Помилка отримання сесій: {e}")
        finally:
            conn.close()
        return sessions

    def get_tests(self, teacher_id: int = None) -> List[Dict[str, Any]]:
        tests = []
        conn = self._get_connection()
        if not conn: return tests
        try:
            cursor = conn.cursor()
            t_cond = f"AND t.teacher_id = {teacher_id}" if teacher_id else ""
            query = f"""
                SELECT 
                    t.id,
                    t.title,
                    t.subject,
                    t.class_name,
                    t.created_at,
                    te.id as teacher_id,
                    te.username as teacher_username,
                    te.full_name as teacher_name,
                    (SELECT COUNT(*) FROM questions q WHERE q.test_id = t.id) as total_questions,
                    (SELECT COUNT(*) FROM test_sessions ts WHERE ts.test_id = t.id AND ts.is_active = 1) as active_sessions
                FROM tests t
                JOIN teachers te ON t.teacher_id = te.id
                WHERE (t.is_archived IS NULL OR t.is_archived = 0)
                {t_cond}
                ORDER BY t.id DESC
            """
            cursor.execute(query)
            for row in cursor.fetchall():
                tests.append({
                    "id": row[0],
                    "title": row[1],
                    "subject": row[2] or "—",
                    "class_name": row[3] or "—",
                    "created_at": str(row[4])[:19] if row[4] else "—",
                    "teacher_id": row[5],
                    "teacher_username": row[6],
                    "teacher_name": row[7] or row[6],
                    "total_questions": row[8] or 0,
                    "active_sessions": row[9] or 0
                })
        except Exception as e:
            print(f"Помилка отримання тестів: {e}")
        finally:
            conn.close()
        return tests

    def create_session(self, test_id: int) -> Dict[str, Any]:
        """Створює нову сесію тестування безпосередньо в БД та генерує унікальний код доступу."""
        conn = self._get_connection()
        if not conn:
            return {"success": False, "error": "Немає підключення до БД"}
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT title, (SELECT COUNT(*) FROM questions q WHERE q.test_id = t.id) FROM tests t WHERE t.id = ?", (test_id,))
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": "Тест не знайдено"}
            test_title, q_count = row[0], row[1]
            if q_count == 0:
                return {"success": False, "error": "У цьому тесті немає запитань. Додайте питання у браузері."}
                
            import random, string
            access_code = ""
            for _ in range(30):
                code = "".join(random.choices(string.digits, k=6))
                cursor.execute("SELECT id FROM test_sessions WHERE access_code = ? AND is_active = 1", (code,))
                if not cursor.fetchone():
                    access_code = code
                    break
            if not access_code:
                access_code = "".join(random.choices(string.digits, k=8))
                
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO test_sessions (test_id, access_code, started_at, is_active, is_archived)
                VALUES (?, ?, ?, 1, 0)
            """, (test_id, access_code, now_str))
            session_id = cursor.lastrowid
            conn.commit()
            return {
                "success": True,
                "session_id": session_id,
                "access_code": access_code,
                "test_title": test_title
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def get_test_results_summary(self, test_id: int) -> Dict[str, Any]:
        """Повертає базову статистику проходження тесту користувачами."""
        res = {
            "test_id": test_id,
            "title": "",
            "max_grade": 12,
            "total_attempts": 0,
            "completed_attempts": 0,
            "avg_score": 0.0,
            "avg_percent": 0.0,
            "attempts": []
        }
        conn = self._get_connection()
        if not conn: return res
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT title, max_grade FROM tests WHERE id = ?", (test_id,))
            row = cursor.fetchone()
            if not row: return res
            res["title"], res["max_grade"] = row[0], row[1] or 12
            
            query = """
                SELECT 
                    a.id,
                    a.student_name,
                    a.started_at,
                    a.finished_at,
                    a.status,
                    a.score,
                    a.max_score,
                    s.access_code
                FROM student_attempts a
                JOIN test_sessions s ON a.session_id = s.id
                WHERE s.test_id = ?
                ORDER BY a.started_at DESC
            """
            cursor.execute(query, (test_id,))
            rows = cursor.fetchall()
            
            status_map = {
                'not_started': 'Очікує',
                'in_progress': 'В процесі',
                'finished': 'Завершено',
                'timeout': 'Час вийшов',
                'paused': 'Призупинено',
                'stopped': 'Зупинено'
            }
            
            total_score_sum = 0.0
            completed_cnt = 0
            
            for r in rows:
                att_id, name, started_at, finished_at, status, score, max_score, access_code = r
                status_ua = status_map.get(status, status)
                
                score_val = score or 0.0
                max_val = max_score or 1.0
                percent = round((score_val / max_val) * 100, 1) if max_val > 0 else 0.0
                
                if status in ('finished', 'timeout', 'stopped'):
                    completed_cnt += 1
                    total_score_sum += score_val
                    
                res["attempts"].append({
                    "id": att_id,
                    "student_name": name,
                    "started_at": str(started_at)[:19] if started_at else "—",
                    "finished_at": str(finished_at)[:19] if finished_at else "—",
                    "status": status_ua,
                    "raw_status": status,
                    "score": round(score_val, 2),
                    "max_score": round(max_val, 2),
                    "percent": percent,
                    "access_code": access_code
                })
                
            res["total_attempts"] = len(rows)
            res["completed_attempts"] = completed_cnt
            if completed_cnt > 0:
                res["avg_score"] = round(total_score_sum / completed_cnt, 2)
                res["avg_percent"] = round((res["avg_score"] / res["max_grade"]) * 100, 1)
        except Exception as e:
            print(f"Помилка отримання результатів тесту: {e}")
        finally:
            conn.close()
        return res


