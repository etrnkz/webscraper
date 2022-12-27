"""Performance monitoring and optimization"""
import time
import logging
from functools import wraps
from collections import defaultdict

logger = logging.getLogger(__name__)

# Performance metrics
request_times = defaultdict(list)
cache_hits = 0
cache_misses = 0


def measure_time(func):
    """Decorator to measure function execution time"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start
        
        request_times[func.__name__].append(duration)
        logger.debug(f"{func.__name__} took {duration:.2f}s")
        
        return result
    return wrapper


def get_average_time(func_name):
    """Get average execution time for a function"""
    times = request_times.get(func_name, [])
    if not times:
        return 0
    return sum(times) / len(times)


def get_performance_stats():
    """Get overall performance statistics"""
    stats = {
        'total_requests': sum(len(times) for times in request_times.values()),
        'cache_hit_rate': 0,
        'average_times': {}
    }
    
    # Calculate cache hit rate
    total_cache_requests = cache_hits + cache_misses
    if total_cache_requests > 0:
        stats['cache_hit_rate'] = (cache_hits / total_cache_requests) * 100
    
    # Average times per function
    for func_name, times in request_times.items():
        if times:
            stats['average_times'][func_name] = sum(times) / len(times)
    
    return stats


def record_cache_hit():
    """Record a cache hit"""
    global cache_hits
    cache_hits += 1


def record_cache_miss():
    """Record a cache miss"""
    global cache_misses
    cache_misses += 1
