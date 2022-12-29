"""Admin panel functionality for user monitoring and management"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class AdminPanel:
    def __init__(self):
        self.user_data = defaultdict(lambda: {
            'first_seen': None,
            'last_seen': None,
            'total_requests': 0,
            'total_errors': 0,
            'blocked_count': 0,
            'username': None,
            'first_name': None,
            'is_active': True
        })
    
    def register_user(self, user_id, username=None, first_name=None):
        """Register or update user information"""
        now = datetime.now()
        
        if not self.user_data[user_id]['first_seen']:
            self.user_data[user_id]['first_seen'] = now
            logger.info(f"New user registered: {user_id}")
        
        self.user_data[user_id]['last_seen'] = now
        self.user_data[user_id]['username'] = username
        self.user_data[user_id]['first_name'] = first_name
    
    def record_request(self, user_id, success=True):
        """Record a user request"""
        self.user_data[user_id]['total_requests'] += 1
        if not success:
            self.user_data[user_id]['total_errors'] += 1
    
    def record_block(self, user_id):
        """Record a blocked request"""
        self.user_data[user_id]['blocked_count'] += 1
    
    def get_user_info(self, user_id):
        """Get detailed user information"""
        return self.user_data.get(user_id)
    
    def get_all_users(self):
        """Get all registered users"""
        return dict(self.user_data)
    
    def get_active_users(self, hours=24):
        """Get users active in the last N hours"""
        cutoff = datetime.now() - timedelta(hours=hours)
        active = []
        
        for user_id, data in self.user_data.items():
            if data['last_seen'] and data['last_seen'] > cutoff:
                active.append((user_id, data))
        
        return active
    
    def get_top_users(self, limit=10):
        """Get top users by request count"""
        sorted_users = sorted(
            self.user_data.items(),
            key=lambda x: x[1]['total_requests'],
            reverse=True
        )
        return sorted_users[:limit]
    
    def ban_user(self, user_id):
        """Ban a user"""
        self.user_data[user_id]['is_active'] = False
        logger.warning(f"User banned: {user_id}")
    
    def unban_user(self, user_id):
        """Unban a user"""
        self.user_data[user_id]['is_active'] = True
        logger.info(f"User unbanned: {user_id}")
    
    def is_banned(self, user_id):
        """Check if user is banned"""
        return not self.user_data[user_id]['is_active']
    
    def get_statistics(self):
        """Get overall statistics"""
        total_users = len(self.user_data)
        active_24h = len(self.get_active_users(24))
        total_requests = sum(u['total_requests'] for u in self.user_data.values())
        total_errors = sum(u['total_errors'] for u in self.user_data.values())
        total_blocks = sum(u['blocked_count'] for u in self.user_data.values())
        
        return {
            'total_users': total_users,
            'active_24h': active_24h,
            'total_requests': total_requests,
            'total_errors': total_errors,
            'total_blocks': total_blocks,
            'avg_requests_per_user': total_requests / total_users if total_users > 0 else 0
        }
    
    def format_user_list(self, users, limit=10):
        """Format user list for display"""
        lines = []
        for i, (user_id, data) in enumerate(users[:limit], 1):
            name = data['first_name'] or data['username'] or f"User {user_id}"
            requests = data['total_requests']
            errors = data['total_errors']
            status = "🚫" if not data['is_active'] else "✅"
            
            lines.append(f"{i}. {status} {name} - {requests} req ({errors} err)")
        
        return '\n'.join(lines) if lines else "No users found"


# Global admin panel instance
admin_panel = AdminPanel()
