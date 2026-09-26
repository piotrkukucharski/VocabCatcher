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

## 2. Praca z niezależnymi obszarami roboczymi (Workspaces w `jj`)

Zgodnie z zasadą projektu, do tworzenia niezależnych drzew roboczych używamy **`jj workspace`** (zamiast `git worktree`):

### Tworzenie nowego workspace w `jj`:
```bash
# Utworzenie nowego workspace dla gałęzi/funkcjonalności w równoległym katalogu
jj workspace add ../VocabCatcher-auth --name auth -r main -m "feat(auth): initial setup"
```

### Lista aktywnych przestrzeni roboczych:
```bash
jj workspace list
```

### Inicjalizacja środowiska w nowym workspace:
```bash
cd ../VocabCatcher-auth
cp ../VocabCatcher-main/.env .env
task install
```

### Usunięcie workspace po zakończeniu pracy:
```bash
jj workspace forget auth
rm -rf ../VocabCatcher-auth
```
