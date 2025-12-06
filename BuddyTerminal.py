import asyncio
from datetime import datetime, timezone, timedelta
import traceback
import json

from aiohttp import web
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, DataTable, Static, Input
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable
from textual.screen import ModalScreen


class TaskServer:
    def __init__(self, update_callback):
        self.update_callback = update_callback
        self.current_tasks = []
        self.sse_clients = []
        self.last_date_check = None
        self.app = web.Application()
        self.app.add_routes([
            web.options('/tasks', self.handle_options),
            web.post('/tasks', self.handle_tasks),
            web.get('/tasks', self.handle_get_tasks),
            web.get('/tasks/stream', self.handle_sse),
            web.get('/ping', self.handle_ping),
            web.options('/ping', self.handle_options),
        ])
        self.runner = None
        self.site = None

    def should_update_dates(self):
        """Check if we should update dates (only once per day)"""
        now = datetime.now(timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        if self.last_date_check is None or self.last_date_check < today:
            self.last_date_check = now
            return True
        return False

    def update_task_dates_to_today(self, tasks):
        """Update task dates to today if they're from a previous day"""
        if not isinstance(tasks, list):
            return tasks

        if not self.should_update_dates():
            return tasks

        now = datetime.now(timezone.utc)
        today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        changed = False
        for task in tasks:
            due_time_str = task.get('dueTime')
            if not due_time_str:
                continue

            try:
                due_time_str = due_time_str.replace('Z', '+00:00')
                task_due = datetime.fromisoformat(due_time_str)
                if task_due.tzinfo is None:
                    task_due = task_due.replace(tzinfo=timezone.utc)

                task_due_date = datetime(task_due.year, task_due.month, task_due.day, tzinfo=timezone.utc)

                if task_due_date < today:
                    new_due_time = datetime(
                        today.year, today.month, today.day,
                        task_due.hour, task_due.minute, task_due.second, task_due.microsecond,
                        tzinfo=timezone.utc
                    )
                    task['dueTime'] = new_due_time.isoformat()
                    task['alarmTriggered'] = False
                    task['checked'] = False
                    changed = True
                    print(f"✓ Updated task '{task.get('name')}' date from {task_due_date.date()} to {today.date()}")

            except Exception as e:
                print(f"Error updating date for task {task.get('name')}: {e}")
                continue

        return tasks

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', 2137)
        await self.site.start()
        print(f"DEBUG: Server started on port 2137 with SSE support")

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

    async def handle_sse(self, request):
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Access-Control-Allow-Origin': '*',
                'Connection': 'keep-alive',
            }
        )
        await response.prepare(request)

        self.sse_clients.append(response)
        print(f"✓ SSE client connected (total: {len(self.sse_clients)})")

        try:
            if self.current_tasks and self.should_update_dates():
                print("🔄 First SSE connection today - checking if dates need updating...")
                self.current_tasks = self.update_task_dates_to_today(self.current_tasks)

            await response.write(f"data: {json.dumps(self.current_tasks)}\n\n".encode('utf-8'))

            while True:
                await asyncio.sleep(30)
                try:
                    await response.write(": keepalive\n\n".encode('utf-8'))
                except:
                    break
        except Exception as e:
            print(f"SSE client disconnected: {e}")
        finally:
            if response in self.sse_clients:
                self.sse_clients.remove(response)
            print(f"✓ SSE client removed (remaining: {len(self.sse_clients)})")

        return response

    async def broadcast_tasks(self):
        """Send updated tasks to all connected browsers via SSE"""
        if not self.sse_clients:
            return

        message = f"data: {json.dumps(self.current_tasks)}\n\n".encode('utf-8')
        disconnected = []

        for client in self.sse_clients:
            try:
                await client.write(message)
            except Exception as e:
                print(f"Failed to send to SSE client: {e}")
                disconnected.append(client)

        for client in disconnected:
            if client in self.sse_clients:
                self.sse_clients.remove(client)

    async def handle_tasks(self, request):
        try:
            data = await request.json()
            self.current_tasks = data
            self.update_callback(self.current_tasks)

            await self.broadcast_tasks()

            return web.Response(text="Received", headers={'Access-Control-Allow-Origin': '*'})
        except Exception as e:
            traceback.print_exc()
            return web.Response(status=500, text=str(e), headers={'Access-Control-Allow-Origin': '*'})


# Modal screen for editing task name
class EditTaskScreen(ModalScreen):
    CSS = """
    EditTaskScreen {
        align: center middle;
    }

    #edit-dialog {
        width: 60;
        height: 11;
        background: #2a2a2a;
        border: heavy #4a4a4a;
        padding: 1 2;
    }

    #edit-title {
        width: 100%;
        height: 3;
        content-align: center middle;
        text-style: bold;
        color: #49dfb7;
    }

    #edit-input {
        width: 100%;
        margin-top: 1;
        margin-bottom: 1;
    }

    #edit-help {
        width: 100%;
        height: 2;
        content-align: center middle;
        color: #a0a0a0;
    }
    """

    def __init__(self, task_name: str):
        super().__init__()
        self.task_name = task_name
        self.new_name = None

    def compose(self) -> ComposeResult:
        with Vertical(id="edit-dialog"):
            yield Static("Edit Task Name", id="edit-title")
            yield Input(value=self.task_name, placeholder="Task name", id="edit-input")
            yield Static("Press Enter to save, Escape to cancel", id="edit-help")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.new_name = event.value
        self.dismiss(self.new_name)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


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

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

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

    def on_key(self, event) -> None:
        """Handle key presses globally"""
        if event.key == "a":
            self.adjust_task_time(-1)
            event.prevent_default()
            event.stop()
        elif event.key == "d":
            self.adjust_task_time(1)
            event.prevent_default()
            event.stop()
        elif event.key == "u":
            asyncio.create_task(self.action_edit_task_name())
            event.prevent_default()
            event.stop()
        elif event.key == "up":
            # Allow up arrow to move cursor normally
            pass
        elif event.key == "down":
            # Allow down arrow to move cursor normally
            pass

    async def on_unmount(self) -> None:
        """Cleanup when the app is closed (Ctrl+C or 'q')."""
        if hasattr(self, 'server'):
            await self.server.stop()

    def update_tasks(self, tasks):
        self.call_later(self._refresh_table, tasks)

    def _refresh_table(self, tasks):
        table = self.query_one(DataTable)

        # Save current row key (if any)
        previous_key = None
        if table.cursor_row is not None and table.row_count:
            for idx, key in enumerate(table.rows.keys()):
                if idx == table.cursor_row:
                    previous_key = key
                    break

        # Clear once, before repopulating
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

        # Restore cursor by key after table is fully rebuilt
        if previous_key is not None and table.row_count:
            for idx, key in enumerate(table.rows.keys()):
                if key == previous_key:
                    table.move_cursor(row=idx)
                    break

        local_now = datetime.now().strftime("%H:%M:%S")
        self.query_one("#status").update(f"Server Active (Port 2137) • Last Update: {local_now}")

        self.query_one("#stat-total").update(f"[#a0a0a0]Total:[/] [#49dfb7][b]{total_tasks}[/b]")
        self.query_one("#stat-done").update(f"[#a0a0a0]Done:[/] [#50fa7b][b]{done_count}[/b]")
        self.query_one("#stat-todo").update(f"[#a0a0a0]Todo:[/] [#f1fa8c][b]{todo_count}[/b]")
        self.query_one("#stat-overdue").update(f"[#a0a0a0]Overdue:[/] [#ff5555][b]{overdue_count}[/b]")

    def get_selected_task(self):
        """Get the currently selected task"""
        table = self.query_one(DataTable)
        if table.cursor_row is None or table.cursor_row >= table.row_count:
            return None

        try:
            # Get the row key from the cursor position
            row_key = None
            for idx, key in enumerate(table.rows.keys()):
                if idx == table.cursor_row:
                    row_key = key
                    break

            if row_key is None:
                return None

            task_id = str(row_key.value)

            for task in self.server.current_tasks:
                if str(task.get("id")) == task_id:
                    return task
        except Exception as e:
            print(f"Error getting selected task: {e}")
            import traceback
            traceback.print_exc()

        return None

    def adjust_task_time(self, minutes_delta: int):
        """Adjust the selected task's time by the given number of minutes"""
        task = self.get_selected_task()
        if not task:
            print("No task selected")
            return

        # Save current cursor position
        table = self.query_one(DataTable)
        current_row = table.cursor_row

        due_str = task.get('dueTime')
        if not due_str:
            print("Task has no due time")
            return

        try:
            # Parse current due time
            due_str = due_str.replace('Z', '+00:00')
            due_time = datetime.fromisoformat(due_str)
            if due_time.tzinfo is None:
                due_time = due_time.replace(tzinfo=timezone.utc)

            # Add minutes (timedelta handles hour rollover automatically)
            new_due_time = due_time + timedelta(minutes=minutes_delta)

            # Update task
            task['dueTime'] = new_due_time.isoformat()
            task['isPreset'] = False  # Mark as manually edited

            print(f"✓ Adjusted '{task['name']}' time by {minutes_delta} min: {new_due_time.strftime('%H:%M')}")

            # Refresh display and broadcast
            self._refresh_table(self.server.current_tasks)
            asyncio.create_task(self.sync_tasks_to_webapp())

            # Restore cursor position after refresh
            if current_row is not None and current_row < table.row_count:
                table.move_cursor(row=current_row)

        except Exception as e:
            print(f"Error adjusting time: {e}")

    async def action_decrease_time(self):
        """Decrease selected task time by 1 minute"""
        self.adjust_task_time(-1)

    async def action_increase_time(self):
        """Increase selected task time by 1 minute"""
        self.adjust_task_time(1)

    async def action_edit_task_name(self):
        """Edit the selected task's name"""
        task = self.get_selected_task()
        if not task:
            print("No task selected")
            return

        # Save current cursor position
        table = self.query_one(DataTable)
        current_row = table.cursor_row

        current_name = task.get('name', '')

        # Show modal screen for editing
        result = await self.push_screen_wait(EditTaskScreen(current_name))

        if result and result.strip():  # User pressed Enter with a non-empty value
            task['name'] = result.strip()
            task['isPreset'] = False  # Mark as manually edited
            print(f"✓ Updated task name to: {result}")

            # Refresh display and broadcast
            self._refresh_table(self.server.current_tasks)
            asyncio.create_task(self.sync_tasks_to_webapp())

            # Restore cursor position and focus after refresh
            if current_row is not None and current_row < table.row_count:
                table.move_cursor(row=current_row)
            table.focus()
        else:
            print("Task name update cancelled or empty")
            # Just restore focus if cancelled
            table.focus()

    async def sync_tasks_to_webapp(self):
        """Send updated tasks back to the web app on port 2137"""
        try:
            await self.server.broadcast_tasks()
            print("✓ Broadcasted to all connected browsers")
        except Exception as e:
            print(f"Broadcast failed: {e}")

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

                asyncio.create_task(self.sync_tasks_to_webapp())

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

                asyncio.create_task(self.sync_tasks_to_webapp())

                if current_row < table.row_count:
                    table.move_cursor(row=current_row)
                return


if __name__ == "__main__":
    app = TaskBuddyApp()
    app.run()