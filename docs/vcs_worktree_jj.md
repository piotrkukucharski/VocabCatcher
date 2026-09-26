# Praca z Git Worktree oraz Jujutsu (jj)

Projekt VocabCatcher jest w pełni przystosowany do pracy z **Git worktrees** oraz **Jujutsu (`jj`)** w trybie współdzielonym (`colocated repo`).

---

## 1. Praca z Jujutsu (`jj`)

Repozytorium zostało zainicjowane z flagą `--colocate`, co oznacza, że `.jj` oraz standardowy katalog `.git` współistnieją bezpośrednio w projekcie. Zmiany wykonane w `jj` natychmiast odzwierciedlają się w gicie i na odwrót.

### Najważniejsze komendy jj:
- `jj status` – podgląd aktualnej zmiany (working-copy commit).
- `jj log` – przegląd grafu rewizji (nieblokujący: `jj --no-pager log`).
- `jj describe -m "komunikat"` – opis bieżącego commita.
- `jj new` – utworzenie nowej pustej gałęzi/rewizji.
- `jj bookmark create <nazwa> -r @-` – stworzenie wskaźnika odpowiadającego gałęzi git (np. `main` lub feature branch).
- `jj git push` – wypchnięcie zmian do zdalnego repozytorium git.

---

## 2. Praca z Git Worktree

Dzięki temu, że zależności i środowiska wirtualne (`.venv`, `web/node_modules`) są izolowane per-folder i ignorowane w `.gitignore`, można bezpiecznie tworzyć niezależne drzewa robocze:

### Tworzenie nowego worktree:
```bash
# Utworzenie nowego worktree dla gałęzi feature-x w równoległym katalogu
git worktree add ../VocabCatcher-feature-x -b feature-x
```

### Inicjalizacja środowiska w nowym worktree:
```bash
cd ../VocabCatcher-feature-x
cp ../VocabCatcher/.env .env
task install
```

### Usunięcie worktree po zakończeniu pracy:
```bash
git worktree remove ../VocabCatcher-feature-x
```
