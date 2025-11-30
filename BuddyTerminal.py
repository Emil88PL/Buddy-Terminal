import asyncio
from datetime import datetime, timezone
import traceback

from aiohttp import web
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Horizontal
# NOTE: DataTable is used for type hinting RowSelected/CellSelected messages
from textual.widgets import DataTable


# --- Server Logic ---
# (TaskServer class remains unchanged from your last working version)
class TaskServer:
    def __init__(self, update_callback):
        self.update_callback = update_callback
        self.current_tasks = []
        self.app = web.Application()
        self.app.add_routes([
            web.options('/tasks', self.handle_options),
            web.post('/tasks', self.handle_tasks),
            web.get('/tasks', self.handle_get_tasks),
            web.get('/ping', self.handle_ping),
            web.options('/ping', self.handle_options),
        ])
        self.runner = None
        self.site = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', 2137)
        await self.site.start()
        print(f"DEBUG: Server started on port 2137")

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        print(f"DEBUG: Server stopped")

    async def handle_options(self, request):
        return web.Response(headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    async def handle_ping(self, request):
        return web.Response(text="pong", headers={'Access-Control-Allow-Origin': '*'})

    async def handle_get_tasks(self, request):
        return web.json_response(self.current_tasks, headers={'Access-Control-Allow-Origin': '*'})

    async def handle_tasks(self, request):
        try:
            data = await request.json()
            self.current_tasks = data
            self.update_callback(data)
            return web.Response(text="Received", headers={'Access-Control-Allow-Origin': '*'})
        except Exception as e:
            traceback.print_exc()
            return web.Response(status=500, text=str(e), headers={'Access-Control-Allow-Origin': '*'})


# --- Textual UI Logic ---
class TaskBuddyApp(App):
    CSS = """
    Screen { background: #373636; align: center middle; }
    DataTable { height: 100%; background: #2a2a2a; border: heavy #4a4a4a; color: #ffffff; }
    DataTable > .datatable--header { background: #3a3a3a; color: #a0a0a0; text-style: bold; }
    DataTable > .datatable--cursor { background: #404040; }
    DataTable > .datatable--odd-row { background: #2d2d2d; }
    DataTable > .datatable--even-row { background: #323232; }

    .status-box { dock: top; height: 3; content-align: center middle; background: #3a3a3a; color: #49dfb7; text-style: bold; border: heavy #4a4a4a; margin-bottom: 1; }
    .stats-container { dock: top; height: 3; margin-bottom: 1; }
    .stat-box { width: 1fr; height: 100%; content-align: center middle; background: #3a3a3a; border: heavy #4a4a4a; margin-right: 1; }
    .stat-box:last-child { margin-right: 0; }

    Header { background: #2a2a2a; color: #a0a0a0; }
    Footer { background: #373636; color: #a0a0a0; }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Initializing...", id="status", classes="status-box")

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
        table.focus()
        table.zebra_stripes = True

        self.server = TaskServer(self.update_tasks)
        await self.server.start()

        self.query_one("#status").update("Server Running on Port 2137 • Waiting for tasks...")

    async def on_unmount(self) -> None:
        """Cleanup when the app is closed (Ctrl+C or 'q')."""
        if hasattr(self, 'server'):
            await self.server.stop()

    def update_tasks(self, tasks):
        self.call_later(self._refresh_table, tasks)

    def _refresh_table(self, tasks):
        table = self.query_one(DataTable)

        table.clear()

        if not isinstance(tasks, list):
            return

        sorted_tasks = sorted(tasks, key=lambda x: x.get('dueTime', ''))
        current_utc = datetime.now(timezone.utc)

        total_tasks = len(tasks)
        done_count = todo_count = overdue_count = 0

        for task in sorted_tasks:
            name = task.get('name', 'Unknown task')
            is_checked = task.get('checked', False)
            due_str = task.get('dueTime', '')
            task_id = task.get("id", str(hash(name)))

            time_str = "??:??"
            time_passed = False

            if due_str:
                try:
                    due_str = due_str.replace('Z', '+00:00')
                    due_utc = datetime.fromisoformat(due_str)
                    if due_utc.tzinfo is None:
                        due_utc = due_utc.replace(tzinfo=timezone.utc)

                    due_local = due_utc.astimezone()
                    time_str = due_local.strftime("%H:%M")
                    time_passed = due_utc < current_utc
                except Exception:
                    name = f"[dim]{name} (bad date)[/dim]"

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

            table.add_row(status, time_str, style_name, key=task_id)

        local_now = datetime.now().strftime("%H:%M:%S")
        self.query_one("#status").update(f"Server Active (Port 2137) • Last Update: {local_now}")

        self.query_one("#stat-total").update(f"[#a0a0a0]Total:[/] [#49dfb7][b]{total_tasks}[/b]")
        self.query_one("#stat-done").update(f"[#a0a0a0]Done:[/] [#50fa7b][b]{done_count}[/b]")
        self.query_one("#stat-todo").update(f"[#a0a0a0]Todo:[/] [#f1fa8c][b]{todo_count}[/b]")
        self.query_one("#stat-overdue").update(f"[#a0a0a0]Overdue:[/] [#ff5555][b]{overdue_count}[/b]")

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        table = self.query_one(DataTable)
        current_row = event.cursor_row

        task_id = str(event.row_key.value)

        for task in self.server.current_tasks:
            if str(task.get("id")) == task_id:
                task["checked"] = not task.get("checked", False)
                if task["checked"]:
                    task["alarmTriggered"] = False
                print(f"✓ TOGGLED (KEYBOARD): {task['name']} → {'DONE' if task['checked'] else 'TODO'}")
                self._refresh_table(self.server.current_tasks)

                if current_row < table.row_count:
                    table.move_cursor(row=current_row)
                return

    def on_data_table_cell_selected(self, event: DataTable.CellSelected):
        if event.cell_key.row_key is None:
            return

        table = self.query_one(DataTable)
        current_row = event.coordinate.row

        task_id = str(event.cell_key.row_key.value)

        for task in self.server.current_tasks:
            if str(task.get("id")) == task_id:
                task["checked"] = not task.get("checked", False)
                if task["checked"]:
                    task["alarmTriggered"] = False
                print(f"✓ TOGGLED (CLICK): {task['name']} → {'DONE' if task['checked'] else 'TODO'}")
                self._refresh_table(self.server.current_tasks)

                if current_row < table.row_count:
                    table.move_cursor(row=current_row)
                return


if __name__ == "__main__":
    app = TaskBuddyApp()
    app.run()