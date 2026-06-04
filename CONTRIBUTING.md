"""
CONTRIBUTING.md
Contribution Guidelines for WFM-Engine
"""

# Contributing to WFM-Engine

Thank you for your interest in contributing! This document provides guidelines for all contributions.

## 🚀 Getting Started

### 1. Fork & Clone

```bash
git clone https://github.com/yourusername/WFM-Engine.git
cd WFM-Engine
```

### 2. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### 3. Install Development Dependencies

```bash
make install
```

## 📝 Code Standards

All contributions must meet these standards:

### Formatting (Black)

```bash
black app/ tests/ main.py --line-length=100
```

### Linting (Ruff)

```bash
ruff check app/ tests/
```

**Zero tolerance:** All ruff violations must be fixed.

### Type Checking (Mypy)

```bash
mypy app/ main.py --ignore-missing-imports
```

**100% compliance required** for new code.

### Testing (Pytest)

```bash
pytest tests/ -v --cov=app
```

**Minimum 85% coverage** for new features.

## 🏗️ Architecture Guidelines

### Domain Layer (`app/domain/`)

✅ **DO:**
- Use immutable frozen dataclasses
- Define enums for state machines
- Keep domain-pure (no I/O, no external deps)

❌ **DON'T:**
- Import from infrastructure/api layers
- Make HTTP calls
- Access databases
- Use mutable objects

### Use Cases Layer (`app/usecases/`)

✅ **DO:**
- Write pure functions
- No side effects
- All inputs as parameters
- All outputs as return values

❌ **DON'T:**
- Import FastAPI, pydantic_settings, or SQLAlchemy
- Make async calls
- Mutate global state

### Infrastructure Layer (`app/infrastructure/`)

✅ **DO:**
- Implement abstract repositories
- Handle I/O (databases, APIs)
- Manage connections
- Log operations

❌ **DON'T:**
- Import from api layer
- Contain business logic
- Leak implementation details

### API Layer (`app/api/`)

✅ **DO:**
- Define endpoints
- Wire dependency injection
- Handle HTTP concerns
- Validate requests

❌ **DON'T:**
- Put business logic here
- Access databases directly
- Import monolithic modules

## 📚 Commit Messages

Follow conventional commits:

```
feat: Add new feature
fix: Fix a bug
docs: Update documentation
test: Add tests
refactor: Refactor code
perf: Improve performance
chore: Maintenance tasks
```

Example:

```
feat: Add reflector engine for deviation analysis

- Compute forecast deviation percentage
- Classify root cause (volume spike, AHT drift, etc.)
- Generate TMK heuristic entries
- Detect uncorrectable error loops

Closes #42
```

## 🧪 Testing Requirements

### Unit Tests

All business logic must be unit testable:

```python
def test_erlang_c_basic():
    """Test basic Erlang C computation."""
    C = erlang_c_probability(A=10.5, N=25)
    assert 0.0 <= C <= 1.0
```

### Integration Tests

For repository & API changes:

```python
@pytest.mark.asyncio
async def test_tmk_repository_write_read():
    """Test TMK write and read cycle."""
    repo = SQLiteTMKRepository(":memory:")
    entry = TMKEntry(...)
    await repo.write_entry(entry)
    loaded = await repo.load_active_entries(...)
    assert entry.entry_id in [e.entry_id for e in loaded]
```

### No Monolithic Tests

Don't test multiple layers in one test. Use mocks:

```python
# ❌ BAD: Tests too much
def test_api_endpoint():
    response = client.post("/ingest", json={...})
    # This tests API + infrastructure + usecases

# ✅ GOOD: Focused test
def test_erlang_calculator():
    result = erlang_c_probability(A=10.5, N=25)
    assert 0.0 <= result <= 1.0
```

## 📋 Pull Request Process

1. **Update** your branch: `git pull origin main`
2. **Run validation:** `make validate`
3. **Commit changes:** `git commit -m "feat: ..."`
4. **Push branch:** `git push origin feature/your-feature`
5. **Open PR** on GitHub
6. **Add description** of changes and motivation
7. **Link issues:** "Closes #123"
8. **Wait for CI** to pass (GitHub Actions)
9. **Address feedback** from reviewers

### PR Checklist

- [ ] Code follows style guide (black, ruff, mypy)
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No breaking changes to API
- [ ] Commit messages are clear
- [ ] Related issues are linked

## 🎯 Areas for Contribution

### High Priority

- [ ] Migrate Reflector logic to `app/usecases/reflector_engine.py`
- [ ] Migrate Router logic to `app/usecases/router_engine.py`
- [ ] Implement HITL queue in `app/infrastructure/`
- [ ] Add PII scrubber in `app/infrastructure/`
- [ ] Database migration framework

### Medium Priority

- [ ] Web dashboard (React/Vue)
- [ ] Observability (Prometheus, Grafana)
- [ ] Rate limiting middleware
- [ ] Request validation middleware
- [ ] Webhook delivery reliability

### Low Priority

- [ ] Performance profiling
- [ ] Load testing framework
- [ ] Documentation improvements
- [ ] CLI tools
- [ ] Example deployments

## 🐛 Reporting Bugs

**Do:**
- Use GitHub Issues
- Describe steps to reproduce
- Include error messages
- Mention Python version & OS

**Example:**

```
Title: Health check endpoint returns 500 error

Steps:
1. Start server: make up
2. Call health endpoint: curl http://localhost:8000/health
3. Observe error

Expected: {"status":"alive",...}
Actual: 500 Internal Server Error

Environment:
- Python 3.11.2
- FastAPI 0.110.1
- Ubuntu 22.04
```

## 💬 Discussions

Use GitHub Discussions for:
- Feature proposals
- Architecture discussions
- Best practices
- General questions

## 📞 Need Help?

- 📖 Read REFACTORING_GUIDE.md
- 💭 Check closed issues/PRs
- 📧 Email maintainer: hatemismail2011@gmail.com

---

**Thank you for contributing to WFM-Engine! 🎉**
