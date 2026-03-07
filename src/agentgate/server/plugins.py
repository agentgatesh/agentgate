"""Plugin system for AgentGate — pre/post task hooks.

Plugins register async callbacks that run before and/or after task routing.
Pre-hooks can modify the task payload or reject the request.
Post-hooks can inspect the result and perform side effects.

Usage:
    from agentgate.server.plugins import plugin_manager

    @plugin_manager.pre_task
    async def log_task(context):
        print(f"Task for {context['agent_name']}: {context['task']}")

    @plugin_manager.post_task
    async def audit_result(context):
        print(f"Result: {context['status']}, latency: {context['latency_ms']}ms")

Context dict for pre_task hooks:
    agent_id, agent_name, task, client_ip

Context dict for post_task hooks:
    agent_id, agent_name, task, client_ip, status, latency_ms, response (if success)
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger("agentgate.plugins")

HookFn = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any] | None]]


class PluginManager:
    """Manages pre/post task hooks."""

    def __init__(self):
        self._pre_hooks: list[HookFn] = []
        self._post_hooks: list[HookFn] = []

    def pre_task(self, fn: HookFn) -> HookFn:
        """Decorator to register a pre-task hook."""
        self._pre_hooks.append(fn)
        logger.info("Registered pre-task plugin: %s", fn.__name__)
        return fn

    def post_task(self, fn: HookFn) -> HookFn:
        """Decorator to register a post-task hook."""
        self._post_hooks.append(fn)
        logger.info("Registered post-task plugin: %s", fn.__name__)
        return fn

    def add_pre_hook(self, fn: HookFn) -> None:
        """Programmatically add a pre-task hook."""
        self._pre_hooks.append(fn)

    def add_post_hook(self, fn: HookFn) -> None:
        """Programmatically add a post-task hook."""
        self._post_hooks.append(fn)

    def remove_pre_hook(self, fn: HookFn) -> None:
        """Remove a pre-task hook."""
        self._pre_hooks = [h for h in self._pre_hooks if h is not fn]

    def remove_post_hook(self, fn: HookFn) -> None:
        """Remove a post-task hook."""
        self._post_hooks = [h for h in self._post_hooks if h is not fn]

    def clear(self) -> None:
        """Remove all hooks."""
        self._pre_hooks.clear()
        self._post_hooks.clear()

    @property
    def pre_hooks(self) -> list[HookFn]:
        return list(self._pre_hooks)

    @property
    def post_hooks(self) -> list[HookFn]:
        return list(self._post_hooks)

    async def run_pre_hooks(self, context: dict[str, Any]) -> dict[str, Any]:
        """Run all pre-task hooks. Returns (possibly modified) context.

        If a hook returns a dict, it replaces the context for subsequent hooks.
        If a hook raises an exception, the task is rejected.
        """
        for hook in self._pre_hooks:
            try:
                result = await hook(context)
                if result is not None:
                    context = result
            except Exception:
                logger.exception("Pre-task plugin %s failed", hook.__name__)
                raise
        return context

    async def run_post_hooks(self, context: dict[str, Any]) -> None:
        """Run all post-task hooks. Exceptions are logged but don't affect the response."""
        for hook in self._post_hooks:
            try:
                await hook(context)
            except Exception:
                logger.exception("Post-task plugin %s failed", hook.__name__)


    def load_from_config(self, config_path: str) -> int:
        """Load plugins from a YAML config file.

        Config format:
            plugins:
              - module: mypackage.myplugin
                hook: pre_task
                function: my_pre_hook
              - module: mypackage.audit
                hook: post_task
                function: audit_result

        Returns the number of plugins loaded.
        """
        import importlib
        from pathlib import Path

        import yaml

        path = Path(config_path)
        if not path.exists():
            logger.warning("Plugin config not found: %s", config_path)
            return 0

        with open(path) as f:
            data = yaml.safe_load(f)

        if not data or "plugins" not in data:
            logger.info("No plugins defined in %s", config_path)
            return 0

        loaded = 0
        for entry in data["plugins"]:
            module_name = entry.get("module")
            func_name = entry.get("function")
            hook_type = entry.get("hook", "pre_task")

            if not module_name or not func_name:
                logger.warning("Skipping invalid plugin entry: %s", entry)
                continue

            try:
                mod = importlib.import_module(module_name)
                fn = getattr(mod, func_name)
                if hook_type == "pre_task":
                    self.add_pre_hook(fn)
                elif hook_type == "post_task":
                    self.add_post_hook(fn)
                else:
                    logger.warning(
                        "Unknown hook type %s for %s.%s",
                        hook_type, module_name, func_name,
                    )
                    continue
                logger.info("Loaded plugin %s.%s as %s", module_name, func_name, hook_type)
                loaded += 1
            except (ImportError, AttributeError) as e:
                logger.error("Failed to load plugin %s.%s: %s", module_name, func_name, e)

        return loaded

    @property
    def plugin_info(self) -> list[dict]:
        """Return info about loaded plugins."""
        return [
            {"name": h.__name__, "type": "pre_task", "module": h.__module__}
            for h in self._pre_hooks
        ] + [
            {"name": h.__name__, "type": "post_task", "module": h.__module__}
            for h in self._post_hooks
        ]


# Global plugin manager instance
plugin_manager = PluginManager()
