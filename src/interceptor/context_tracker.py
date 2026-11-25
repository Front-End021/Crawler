"""Action-Context Tracker for correlating UI events with network requests."""

import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from src.models import ActionContext


class ContextTracker:
    """Thread-safe and async-safe tracker maintaining current UI tab and action state."""

    def __init__(self, initial_tab: str = "General / Landing", initial_action: str = "Initial Page Load"):
        self._current_tab: str = initial_tab
        self._current_action: str = initial_action
        self._current_timestamp: datetime = datetime.now(timezone.utc)
        self._lock = asyncio.Lock()
        self._history: List[ActionContext] = [
            ActionContext(
                tab_name=self._current_tab,
                action_name=self._current_action,
                timestamp=self._current_timestamp,
            )
        ]

    @property
    def current_tab(self) -> str:
        """Get the current UI tab name."""
        return self._current_tab

    @property
    def current_action(self) -> str:
        """Get the current UI action description."""
        return self._current_action

    def get_current_context(self) -> ActionContext:
        """Snapshot current UI state synchronously (used inside high-frequency network hooks)."""
        return ActionContext(
            tab_name=self._current_tab,
            action_name=self._current_action,
            timestamp=self._current_timestamp,
        )

    async def set_tab(self, tab_name: str) -> ActionContext:
        """Update active tab and reset action."""
        async with self._lock:
            self._current_tab = tab_name.strip() or "General / Landing"
            self._current_action = f"Navigate to {self._current_tab}"
            self._current_timestamp = datetime.now(timezone.utc)
            ctx = self.get_current_context()
            self._history.append(ctx)
            return ctx

    async def set_action(self, action_name: str) -> ActionContext:
        """Update active UI action within current tab."""
        async with self._lock:
            self._current_action = action_name.strip() or "Interaction"
            self._current_timestamp = datetime.now(timezone.utc)
            ctx = self.get_current_context()
            self._history.append(ctx)
            return ctx

    async def before_action(self, action_name: str, tab_name: Optional[str] = None) -> ActionContext:
        """Lifecycle hook invoked immediately before executing a UI interaction."""
        async with self._lock:
            if tab_name and tab_name.strip():
                self._current_tab = tab_name.strip()
            self._current_action = action_name.strip() or "UI Interaction"
            self._current_timestamp = datetime.now(timezone.utc)
            ctx = self.get_current_context()
            self._history.append(ctx)
            return ctx

    async def after_action(self, status: str = "Completed") -> None:
        """Lifecycle hook invoked after action execution completes."""
        async with self._lock:
            # Timestamp refreshed for quiet period / idle state
            self._current_timestamp = datetime.now(timezone.utc)

    def get_history(self) -> List[ActionContext]:
        """Return full history of registered UI contexts."""
        return list(self._history)

    def reset(self, tab_name: str = "General / Landing", action_name: str = "Initial Page Load") -> None:
        """Reset context tracker to initial state."""
        self._current_tab = tab_name
        self._current_action = action_name
        self._current_timestamp = datetime.now(timezone.utc)
        self._history = [self.get_current_context()]
