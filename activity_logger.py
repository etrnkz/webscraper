"""Activity logging system for admin monitoring"""
import logging
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)


class ActivityLogger:
    def __init__(self, max_entries=1000):
        self.max_entries = max_entries
        self.activities = deque(maxlen=max_entries)
    
    def log_activity(self, user_id, action, details=None, success=True):
        """Log a user activity"""
        entry = {
            'timestamp': datetime.now(),
            'user_id': user_id,
            'action': action,
            'details': details,
            'success': success
        }
        self.activities.append(entry)
        logger.debug(f"Activity logged: {action} by user {user_id}")
    
    def get_recent_activities(self, limit=50):
        """Get recent activities"""
        return list(self.activities)[-limit:]
    
    def get_user_activities(self, user_id, limit=20):
        """Get activities for a specific user"""
        user_activities = [
            a for a in self.activities
            if a['user_id'] == user_id
        ]
        return user_activities[-limit:]
    
    def get_failed_activities(self, limit=50):
        """Get recent failed activities"""
        failed = [a for a in self.activities if not a['success']]
        return failed[-limit:]
    
    def format_activity(self, activity):
        """Format activity for display"""
        timestamp = activity['timestamp'].strftime('%H:%M:%S')
        user_id = activity['user_id']
        action = activity['action']
        status = "✅" if activity['success'] else "❌"
        
        line = f"{status} {timestamp} | User {user_id} | {action}"
        
        if activity['details']:
            line += f" | {activity['details']}"
        
        return line
    
    def format_activities(self, activities, limit=20):
        """Format multiple activities for display"""
        if not activities:
            return "No activities found"
        
        lines = []
        for activity in activities[-limit:]:
            lines.append(self.format_activity(activity))
        
        return '\n'.join(lines)


# Global activity logger instance
activity_logger = ActivityLogger()
