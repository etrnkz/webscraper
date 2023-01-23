# Changelog

All notable changes to this project will be documented in this file.

## [2.3.0] - 2023-01-23

### Added
- Comprehensive test suite with pytest (helpers, validators, metadata, media extraction)
- GitHub Actions CI/CD workflow with linting and testing
- pyproject.toml for modern Python packaging and tool configuration
- Type hints across all utility and validation functions
- Proper subpackage exports in `__init__.py` files
- Multi-stage Docker build for smaller production images
- Non-root user in Docker container for security

### Fixed
- Critical: `ForceSubscribe` initialization before `bot` client was defined
- Critical: Wrong import `media_scraper` → `media_extractor` in `/media` command
- Critical: Wrong import `performance` module path in `/admin` command
- Critical: Dead code — handlers registered after `bot.run()` would never execute
- Fixed: Nested f-string syntax error in `/userinfo` command
- Fixed: Relative imports in `handlers.py`, `media_extractor.py`, `web_archiver.py`
- Fixed: Missing `__init__.py` exports causing import ambiguity

### Security
- Multi-stage Docker build reduces attack surface
- Non-root user in production container

## [2.0.0] - 2022-12-24

### Added
- Intelligent caching system with 24-hour expiry
- Daily rate limiting (15 requests per day)
- User agent rotation pool for anti-bot detection
- Randomized request headers
- Optional proxy support (HTTP/HTTPS)
- Media scraping functionality (images, videos, CSS, JS)
- Recursive website archiving with `/archive` command
- Robots.txt compliance checking
- Sitemap.xml parsing for URL discovery
- Metadata extraction with `/info` command
- Performance monitoring and metrics
- Cache hit rate tracking
- Exponential backoff retry strategy
- Admin cache management commands

### Changed
- Improved error handling with specific error types
- Enhanced admin panel with performance stats
- Better rate limiting with both per-minute and daily limits
- Upgraded to modular architecture

### Security
- Added localhost and private IP blocking
- Malicious TLD detection
- Robots.txt respect
- Unauthorized access logging

## [1.0.0] - 2022-12-16

### Added
- Basic HTML source code downloading
- Rate limiting (5 requests per minute)
- URL validation
- Error handling and logging
- User statistics tracking
- Admin commands
- Broadcast functionality
- File size limits
- Environment-based configuration

### Security
- Moved credentials to environment variables
- Added input validation
