import asyncio
from datetime import datetime, timezone

from aiohttp import web
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Horizontal


# --- Server Logic ---
class TaskServer:
    def __init__(self, update_callback):
        self.update_callback = update_callback
        self.app = web.Application()
        self.app.add_routes([
            web.options('/tasks', self.handle_options),
            web.post('/tasks', self.handle_tasks),
            web.get('/ping', self.handle_ping),
            web.options('/ping', self.handle_options)
        ])
        self.runner = None
        self.site = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', 2137)
        await self.site.start()

    async def stop(self):
        if self.site:
            await self.runner.cleanup()

    async def handle_options(self, request):
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    async def handle_ping(self, request):
        return web.Response(text="pong", headers={'Access-Control-Allow-Origin': '*'})

    async def handle_tasks(self, request):
        try:
            data = await request.json()
            print(
                f"Received {len(data) if isinstance(data, list) else 'INVALID'} tasks at {datetime.now().strftime('%H:%M:%S')}")
            self.update_callback(data)
            return web.Response(text="Received", headers={'Access-Control-Allow-Origin': '*'})
        except Exception as e:
            print("ERROR in /tasks handler:")
            import traceback
            traceback.print_exc()
            return web.Response(status=500, text=str(e), headers={'Access-Control-Allow-Origin': '*'})


# --- Textual UI Logic ---
class TaskBuddyApp(App):
    CSS = """
    /* Main screen background - matches your website's dark gradient */
    Screen {
        background: #373636;
        align: center middle;
    }

    /* Main data table styling - cleaner grey theme */
    DataTable {
        height: 100%;
        background: #2a2a2a;
        border: heavy #4a4a4a;
        color: #ffffff;
    }

    /* Table column headers - lighter grey with subtle accent */
    DataTable > .datatable--header {
        background: #3a3a3a;
        color: #a0a0a0;
        text-style: bold;
    }

    /* Selected/cursor row - slightly lighter background */
    DataTable > .datatable--cursor {
        background: #404040;
    }

    /* Odd rows - alternating zebra stripes for readability */
    DataTable > .datatable--odd-row {
        background: #2d2d2d;
    }

    /* Even rows - slightly different shade */
    DataTable > .datatable--even-row {
        background: #323232;
    }

    /* Status box showing connection info */
    .status-box {
        dock: top;
        height: 3;
        content-align: center middle;
        background: #3a3a3a;
        color: #49dfb7;
        text-style: bold;
        border: heavy #4a4a4a;
        margin-bottom: 1;
    }

    /* Statistics container - holds all stat boxes */
    .stats-container {
        dock: top;
        height: 3;
        margin-bottom: 1;
    }

    /* Individual stat boxes */
    .stat-box {
        width: 1fr;
        height: 100%;
        content-align: center middle;
        background: #3a3a3a;
        border: heavy #4a4a4a;
        margin-right: 1;
    }

    .stat-box:last-child {
        margin-right: 0;
    }

    /* Stat labels (e.g., "Total:", "Done:") */
    .stat-label {
        color: #a0a0a0;
    }

    /* Stat values (the numbers) */
    .stat-value {
        color: #ffffff;
        text-style: bold;
    }

    /* Color coding for different stat types */
    .stat-total {
        color: #49dfb7;
    }

    .stat-done {
        color: #50fa7b;
    }

    .stat-todo {
        color: #f1fa8c;
    }

    .stat-overdue {
        color: #ff5555;
    }

    /* Top header bar */
    Header {
        background: #2a2a2a;
        color: #a0a0a0;
    }

    /* Bottom footer bar */
    Footer {
        background: #373636;
        color: #a0a0a0;
    }

    /* Keybinding hints in footer */
    Footer > .footer--key {
        background: #3a3a3a;
        color: #ffffff;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.server = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Waiting for connection...", id="status", classes="status-box")

        # Statistics container with individual stat boxes
        with Horizontal(classes="stats-container"):
            yield Static("Total: [b]0[/b]", id="stat-total", classes="stat-box")
            yield Static("Done: [b]0[/b]", id="stat-done", classes="stat-box")
            yield Static("Todo: [b]0[/b]", id="stat-todo", classes="stat-box")
            yield Static("Overdue: [b]0[/b]", id="stat-overdue", classes="stat-box")

        yield DataTable()
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Status", "Time", "Task Name")
        table.cursor_type = "row"
        table.zebra_stripes = True

        self.server = TaskServer(self.update_tasks)
        await self.server.start()
        self.query_one("#status").update("Server Running on Port 2137 • Waiting for tasks...")

    async def on_unmount(self) -> None:
        if self.server:
            await self.server.stop()

    def update_tasks(self, tasks):
        self.call_later(self._refresh_table, tasks)

    def _refresh_table(self, tasks):
        table = self.query_one(DataTable)
        table.clear()

        if not isinstance(tasks, list):
            table.add_row("ERR", "", "Invalid data received")
            self.query_one("#status").update("Connected • Invalid data")
            return

        sorted_tasks = sorted(tasks, key=lambda x: x.get('dueTime', ''))
        current_utc = datetime.now(timezone.utc)

        # Statistics counters
        total_tasks = len(tasks)
        done_count = 0
        todo_count = 0
        overdue_count = 0

        for task in sorted_tasks:
            name = task.get('name', 'Unknown task')
            is_checked = task.get('checked', False)
            due_str = task.get('dueTime', '')

            if not due_str:
                time_str = "??:??"
                time_passed = False
            else:
                try:
                    due_str = due_str.replace('Z', '+00:00')
                    due_utc = datetime.fromisoformat(due_str)
                    if due_utc.tzinfo is None:
                        due_utc = due_utc.replace(tzinfo=timezone.utc)
                    due_local = due_utc.astimezone(tz=None)
                    time_str = due_local.strftime("%H:%M")
                    time_passed = due_utc < current_utc
                except Exception as e:
                    time_str = "??:??"
                    time_passed = False
                    name = f"[dim]{name} (parse error)[/dim]"

            # Update statistics
            if is_checked:
                status = "[green]✔ DONE[/green]"
                style_name = f"[strike]{name}[/strike]"
                done_count += 1
            elif time_passed:
                status = "[red]⚠ LATE[/red]"
                style_name = f"[bold red]{name}[/bold red]"
                overdue_count += 1
            else:
                status = "[yellow]○ TODO[/yellow]"
                style_name = name
                todo_count += 1

            table.add_row(status, time_str, style_name)

        # Update status bar
        local_now = datetime.now().strftime("%H:%M:%S")
        self.query_one("#status").update(
            f"Connected • Updated at {local_now}"
        )

        # Update statistics
        self.query_one("#stat-total").update(f"[#a0a0a0]Total:[/] [#49dfb7][b]{total_tasks}[/b][/]")
        self.query_one("#stat-done").update(f"[#a0a0a0]Done:[/] [#50fa7b][b]{done_count}[/b][/]")
        self.query_one("#stat-todo").update(f"[#a0a0a0]Todo:[/] [#f1fa8c][b]{todo_count}[/b][/]")
        self.query_one("#stat-overdue").update(f"[#a0a0a0]Overdue:[/] [#ff5555][b]{overdue_count}[/b][/]")


if __name__ == "__main__":
    app = TaskBuddyApp()
    app.run()