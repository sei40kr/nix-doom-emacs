;; -*- lexical-binding: t; -*-
;; Nix-generated Doom Emacs profile loader.
;; Loaded by early-init.el after `doom.el' has been required.

(pcase (intern (getenv-internal "DOOMPROFILE"))
  ('@ ;; The default profile in Doom 3.0 (NAME=@, REF=0).
   ;; Resolve the init file via Doom's helper so the path tracks the running
   ;; Emacs version (init.MAJOR.MINOR.el) rather than being hard-coded.
   (load (doom-profile-init-file doom-profile) nil t)

   ;; Workaround: `doom sync', when run during the nix build via nix-straight,
   ;; emits init.MAJOR.MINOR.el with every user module's :index set to the
   ;; final hash-table-count instead of incrementing per `doom-module--put'
   ;; call. Because :depth defaults to (0 . 0), the tie-break on :index
   ;; collapses and `doom--startup-modules' loads modules in hash-table-keys
   ;; order - which puts `:config default' ahead of `:editor evil' and
   ;; crashes startup with `(void-variable evil-window-map)'.
   ;;
   ;; Reassign :index sequentially with `:config' modules pushed last, then
   ;; ask Doom to regenerate `doom--startup-modules' from the corrected
   ;; metadata. Reusing `doom-profile--generate-load-modules' keeps us from
   ;; reimplementing Doom's module-loading shape.
   (doom-require 'doom-lib 'files)
   (doom-require 'doom-lib 'profiles)
   (let ((counter 3)
         (mods (cl-loop for k being the hash-keys of doom-modules
                        using (hash-values v)
                        unless (or (equal k '(:doom))
                                   (equal k '(:user))
                                   (equal k '(:config . use-package)))
                        collect (cons k v))))
     (setq mods (sort mods
                      (lambda (a b)
                        (and (not (eq (caar a) :config))
                             (eq (caar b) :config)))))
     (dolist (entry mods)
       (aset (cdr entry) 1 counter)
       (setq counter (1+ counter))))
   (dolist (form (doom-profile--generate-load-modules))
     (when (and (eq (car-safe form) 'defun)
                (eq (cadr form) 'doom--startup-modules))
       (eval form t)))))

;; Ensure user-emacs-directory ends with a directory separator.
(setq user-emacs-directory (file-name-as-directory user-emacs-directory))
