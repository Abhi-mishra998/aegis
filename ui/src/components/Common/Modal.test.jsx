import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import Modal from './Modal';

describe('Modal', () => {
  afterEach(() => cleanup());

  it('renders nothing when isOpen=false', () => {
    render(<Modal isOpen={false} onClose={() => {}}>hidden</Modal>);
    expect(screen.queryByText('hidden')).toBeNull();
  });

  it('renders title, body, and default close button when isOpen', () => {
    render(
      <Modal isOpen title="Confirm" onClose={() => {}}>
        <p>Body content</p>
      </Modal>,
    );
    expect(screen.getByText('Confirm')).toBeInTheDocument();
    expect(screen.getByText('Body content')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /close dialog/i })).toBeInTheDocument();
  });

  it('calls onClose when the Escape key is pressed', () => {
    const onClose = vi.fn();
    render(<Modal isOpen title="X" onClose={onClose}>body</Modal>);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('sets role="dialog" + aria-modal="true" on the dialog surface', () => {
    render(<Modal isOpen title="Accessible" onClose={() => {}}>body</Modal>);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
  });

  it('renders a footer when provided', () => {
    render(
      <Modal isOpen title="F" onClose={() => {}} footer={<button>Save</button>}>
        body
      </Modal>,
    );
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });
});
