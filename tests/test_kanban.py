"""Tests for the Kanban board system."""

import pytest

from deep_work_assistant.kanban import (
    COLUMNS,
    Card,
    KanbanBoard,
    format_board,
    format_card_list,
)


@pytest.fixture
def board(tmp_path):
    """Create a temporary KanbanBoard for testing."""
    db_path = tmp_path / 'test_kanban.db'
    return KanbanBoard(db_path)


class TestCard:
    def test_minimal_card(self):
        card = Card(card_id='test-1', title='Test card')
        assert card.card_id == 'test-1'
        assert card.column == 'backlog'
        assert card.priority == 0
        assert card.tags == []

    def test_to_dict_roundtrip(self):
        card = Card(
            card_id='test-2',
            title='Build API',
            description='REST endpoints for user module',
            column='in_progress',
            priority=1,
            tags=['backend', 'api'],
            created_at=1000.0,
            updated_at=2000.0,
            session_time_seconds=3600,
            linked_app_pattern='code.exe',
            linked_window_pattern='api',
        )
        data = card.to_dict()
        restored = Card.from_dict(data)
        assert restored.card_id == 'test-2'
        assert restored.title == 'Build API'
        assert restored.column == 'in_progress'
        assert restored.priority == 1
        assert restored.tags == ['backend', 'api']
        assert restored.session_time_seconds == 3600
        assert restored.linked_app_pattern == 'code.exe'

    def test_from_dict_defaults(self):
        data = {'card_id': 'test-3', 'title': 'Simple'}
        card = Card.from_dict(data)
        assert card.column == 'backlog'
        assert card.priority == 0
        assert card.tags == []

    def test_column_label(self):
        card = Card(card_id='t', title='t', column='in_progress')
        assert card.column_label == 'In Progress'

    def test_priority_label(self):
        assert Card(card_id='t', title='t', priority=0).priority_label == 'normal'
        assert Card(card_id='t', title='t', priority=1).priority_label == 'high'
        assert Card(card_id='t', title='t', priority=2).priority_label == 'urgent'


class TestKanbanBoard:
    def test_add_and_get_card(self, board):
        card = Card(card_id='my-card', title='My task')
        board.add_card(card)
        loaded = board.get_card('my-card')
        assert loaded is not None
        assert loaded.title == 'My task'
        assert loaded.card_id == 'my-card'

    def test_add_card_generates_id(self, board):
        card = Card(card_id='', title='Auto ID')
        created = board.add_card(card)
        assert created.card_id
        assert created.card_id.startswith('card-')

    def test_list_cards_empty(self, board):
        assert board.list_cards() == []

    def test_list_cards_with_column_filter(self, board):
        board.add_card(Card(card_id='a', title='A', column='backlog'))
        board.add_card(Card(card_id='b', title='B', column='in_progress'))
        board.add_card(Card(card_id='c', title='C', column='backlog'))

        backlog = board.list_cards(column='backlog')
        assert len(backlog) == 2
        assert all(c.column == 'backlog' for c in backlog)

        in_progress = board.list_cards(column='in_progress')
        assert len(in_progress) == 1

    def test_list_cards_with_tag_filter(self, board):
        board.add_card(Card(card_id='a', title='A', tags=['backend']))
        board.add_card(Card(card_id='b', title='B', tags=['frontend']))
        board.add_card(Card(card_id='c', title='C', tags=['backend', 'urgent']))

        backend = board.list_cards(tag='backend')
        assert len(backend) == 2

        urgent = board.list_cards(tag='urgent')
        assert len(urgent) == 1

    def test_update_card(self, board):
        card = Card(card_id='u', title='Original')
        board.add_card(card)

        card.title = 'Updated'
        card.priority = 2
        board.update_card(card)

        loaded = board.get_card('u')
        assert loaded.title == 'Updated'
        assert loaded.priority == 2

    def test_delete_card(self, board):
        board.add_card(Card(card_id='del', title='Delete me'))
        assert board.get_card('del') is not None
        assert board.delete_card('del') is True
        assert board.get_card('del') is None

    def test_delete_nonexistent(self, board):
        assert board.delete_card('nonexistent') is False

    def test_move_card_valid(self, board):
        board.add_card(Card(card_id='m', title='Move me', column='backlog'))
        result = board.move_card('m', 'ready')
        assert result is not None
        assert result.column == 'ready'
        assert board.get_card('m').column == 'ready'

    def test_move_card_invalid_forward(self, board):
        board.add_card(Card(card_id='m2', title='Move me', column='backlog'))
        # Skipping in_progress directly (not valid)
        result = board.move_card('m2', 'in_progress')
        assert result is None
        # Still in backlog
        assert board.get_card('m2').column == 'backlog'

    def test_move_card_backward_always_allowed(self, board):
        board.add_card(Card(card_id='m3', title='In review', column='review'))
        result = board.move_card('m3', 'in_progress')
        assert result is not None
        assert result.column == 'in_progress'

    def test_move_card_nonexistent(self, board):
        assert board.move_card('noway', 'done') is None

    def test_column_counts(self, board):
        board.add_card(Card(card_id='a', title='A', column='backlog'))
        board.add_card(Card(card_id='b', title='B', column='backlog'))
        board.add_card(Card(card_id='c', title='C', column='in_progress'))
        board.add_card(Card(card_id='d', title='D', column='done'))

        counts = board.column_counts()
        assert counts['backlog'] == 2
        assert counts['in_progress'] == 1
        assert counts['done'] == 1
        assert counts['ready'] == 0
        assert counts['review'] == 0

    def test_total_session_time(self, board):
        board.add_card(Card(card_id='a', title='A', session_time_seconds=3600))
        board.add_card(Card(card_id='b', title='B', session_time_seconds=1800))
        assert board.total_session_time() == 5400

    def test_log_card_session_time(self, board):
        board.add_card(Card(card_id='t', title='Time test'))
        board.log_card_session_time('t', 600)
        assert board.get_card('t').session_time_seconds == 600
        board.log_card_session_time('t', 300)
        assert board.get_card('t').session_time_seconds == 900

    def test_suggest_cards_for_app(self, board):
        board.add_card(Card(
            card_id='api', title='Build API',
            linked_app_pattern='code', column='in_progress',
        ))
        board.add_card(Card(
            card_id='docs', title='Write docs',
            linked_app_pattern='obsidian', column='backlog',
        ))

        suggestions = board.suggest_cards_for_app('Code.exe')
        assert len(suggestions) == 1
        assert suggestions[0].card_id == 'api'

    def test_suggest_cards_uses_window_title(self, board):
        board.add_card(Card(
            card_id='refactor', title='Refactor auth',
            linked_window_pattern='auth', column='in_progress',
        ))
        board.add_card(Card(
            card_id='ui', title='UI polish',
            linked_window_pattern='ui', column='backlog',
        ))

        suggestions = board.suggest_cards_for_app('code.exe', 'auth-module')
        assert len(suggestions) == 1
        assert suggestions[0].card_id == 'refactor'

    def test_search_cards(self, board):
        board.add_card(Card(card_id='1', title='API Gateway', description='Build the gateway'))
        board.add_card(Card(card_id='2', title='Database schema', tags=['db']))
        board.add_card(Card(card_id='3', title='Frontend login page'))

        results = board.search_cards('gateway')
        assert len(results) == 1
        assert results[0].card_id == '1'

        results = board.search_cards('db')
        assert len(results) == 1
        assert results[0].card_id == '2'

        results = board.search_cards('login')
        assert len(results) == 1

    def test_total_cards(self, board):
        assert board.total_cards() == 0
        board.add_card(Card(card_id='a', title='A'))
        assert board.total_cards() == 1
        board.add_card(Card(card_id='b', title='B'))
        assert board.total_cards() == 2

    def test_metadata(self, board):
        board.set_metadata('board_name', 'My Board')
        board.set_metadata('theme', 'dark')
        assert board.get_metadata('board_name') == 'My Board'
        assert board.get_metadata('theme') == 'dark'
        assert board.get_metadata('nonexistent') == ''
        assert board.get_metadata('missing', 'default') == 'default'

    def test_persistence(self, tmp_path):
        """Cards survive board close/reopen."""
        db_path = tmp_path / 'persist.db'
        board1 = KanbanBoard(db_path)
        board1.add_card(Card(card_id='p', title='Persistent'))
        board1.close()

        board2 = KanbanBoard(db_path)
        card = board2.get_card('p')
        assert card is not None
        assert card.title == 'Persistent'
        board2.close()

    def test_multiple_boards_dont_interfere(self, tmp_path):
        db1 = tmp_path / 'board1.db'
        db2 = tmp_path / 'board2.db'

        b1 = KanbanBoard(db1)
        b2 = KanbanBoard(db2)

        b1.add_card(Card(card_id='shared', title='B1 card'))
        b2.add_card(Card(card_id='shared', title='B2 card'))

        assert b1.get_card('shared').title == 'B1 card'
        assert b2.get_card('shared').title == 'B2 card'

        b1.close()
        b2.close()


class TestFormatting:
    def test_format_card_list_empty(self):
        assert 'no cards' in format_card_list([])

    def test_format_card_list_with_cards(self):
        cards = [
            Card(card_id='a', title='Task A', column='in_progress'),
            Card(card_id='b', title='Task B', priority=1),
        ]
        output = format_card_list(cards)
        assert 'Task A' in output
        assert 'Task B' in output
        assert 'In Progress' in output
        assert '↑' in output  # high priority icon

    def test_format_board_empty(self, board):
        output = format_board(board)
        assert 'Backlog' in output
        assert 'Kanban Board' in output

    def test_format_board_with_cards(self, board):
        board.add_card(Card(card_id='c', title='My card', column='in_progress'))
        board.log_card_session_time('c', 3600)
        output = format_board(board)
        assert 'My card' in output
        assert 'In Progress' in output
        assert '60m' in output or '1h' in output