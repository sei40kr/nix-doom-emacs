{ lib, newScope, fetchFromGitHub, fetchFromGitLab, fetchFromGitea, fetchgit
, melpaBuild, elpaBuild, writeText, emacs, trivialBuild
, lock, ocamlPackages, git
}:

let
  # Load generated packages
  generatedPkgs = import ./generated.nix {
    inherit lib newScope fetchFromGitHub fetchFromGitLab fetchFromGitea fetchgit
            melpaBuild elpaBuild writeText emacs trivialBuild;
  };

  # Define overrides
  overrides = self: super: {
    straightBuild = { pname, ... }@args: self.trivialBuild ({
      ename = pname;
      version = "1";
      src = lock pname;
      buildPhase = ":";
    } // args);

    straight = self.trivialBuild {
      pname = "straight";
      ename = "straight";
      version = super.straight.version;
      src = super.straight.src;
      nativeBuildInputs = [ git ];
    };

    all-the-icons = self.trivialBuild {
      pname = "all-the-icons";
      ename = "all-the-icons";
      version = super.all-the-icons.version;
      src = super.all-the-icons.src;
      postInstall = ''
        cp -r $src/data $out/share/emacs/site-lisp/
      '';
    };

    evil-escape = self.trivialBuild {
      pname = "evil-escape";
      ename = "evil-escape";
      version = super.evil-escape.version;
      src = super.evil-escape.src;
      buildPhase = ":";
    };

    elisp-demos = self.trivialBuild {
      pname = "elisp-demos";
      ename = "elisp-demos";
      version = super.elisp-demos.version;
      src = super.elisp-demos.src;
      postInstall = ''
        cp -r $src/*.org $out/share/emacs/site-lisp/ || true
      '';
    };

  doom-snippets = self.straightBuild {
    pname = "doom-snippets";
    postInstall = ''
      cp -r *-mode $out/share/emacs/site-lisp
    '';
  };

  explain-pause-mode = self.straightBuild {
    pname = "explain-pause-mode";
  };

  evil-markdown = self.straightBuild {
    pname = "evil-markdown";
  };

  evil-org = self.straightBuild {
    pname = "evil-org-mode";
    ename = "evil-org";
  };

  evil-quick-diff = self.straightBuild {
    pname = "evil-quick-diff";
  };

  # use-package needs to be built with melpaBuild instead of elpaBuild
  use-package = melpaBuild {
    pname = "use-package";
    version = "20220625.1237";
    commit = "0ad5d9d5d8a61517a207ab04bf69e71c081149eb";

    src = fetchFromGitHub {
      owner = "jwiegley";
      repo = "use-package";
      rev = "0ad5d9d5d8a61517a207ab04bf69e71c081149eb";
      hash = "sha256-nJcSaWcHAanGluVj4rhyHn3jY2i8O8TdpjLHSEgKT4Q=";
    };

    recipe = writeText "recipe" ''
      (use-package :fetcher github :repo "jwiegley/use-package")
    '';

    packageRequires = [ ];
  };

  # git-commit is provided by magit package (via lisp/git-*.el in :files)
  # Create an alias so nix-straight can find it
  git-commit = super.magit;

  magit = super.magit.overrideAttrs (esuper: {
    preBuild = ''
      make VERSION="${esuper.version}" -C lisp magit-version.el
    '';
  });

  nose = self.straightBuild {
    pname = "nose";
  };

  org-contrib = self.straightBuild {
    pname = "org-contrib";
    installPhase = ''
      mkdir -p $out/share/emacs/site-lisp
       cp -r lisp/* $out/share/emacs/site-lisp
    '';
  };

  org = self.straightBuild rec {
    pname = "org";
    version = "9.4";
    installPhase = ''
      LISPDIR=$out/share/emacs/site-lisp
      install -d $LISPDIR

      cp -r * $LISPDIR

      cat > $LISPDIR/lisp/org-version.el <<EOF
      (fset 'org-release (lambda () "${version}"))
      (fset 'org-git-version #'ignore)
      (provide 'org-version)
      EOF
    '';
  };

  org-yt = self.straightBuild {
    pname = "org-yt";
  };

  php-extras = self.straightBuild {
    pname = "php-extras";
  };

  restart-emacs = super.restart-emacs.overrideAttrs (esuper: {
    patches = [ ../../patches/restart-emacs.patch ];
  });

  revealjs = self.straightBuild {
    pname = "revealjs";

    installPhase = ''
      LISPDIR=$out/share/emacs/site-lisp
      install -d $LISPDIR

      cp -r * $LISPDIR
    '';
  };

  rotate-text = self.straightBuild {
    pname = "rotate-text";
  };

  sln-mode = self.straightBuild {
    pname = "sln-mode";
  };

  so-long = self.straightBuild {
    pname = "emacs-so-long";
    ename = "so-long";
  };

  tree-sitter = super.tree-sitter.overrideAttrs (esuper: {
    postInstall = ''
      ln -s ${super.tsc}/share/emacs/site-lisp/elpa/${super.tsc.name}/* \
        $out/share/emacs/site-lisp/elpa/${esuper.pname}-${esuper.version}/
    '';
  });

  ts-fold = self.straightBuild {
    pname = "ts-fold";
  };

  ob-racket = self.straightBuild {
    pname = "ob-racket";
  };

  format-all = self.straightBuild {
    pname = "format-all";
  };

  # dune has a nontrivial derivation, which does not buildable from the melpa
  # wrapper falling back to the one in nixpkgs
  dune = ocamlPackages.dune_2.overrideAttrs (old: {
    # Emacs derivations require an ename attribute
    ename = old.pname;

    # Need to adjust paths here match what doom expects
    postInstall = ''
      mkdir -p $out/share/emacs/site-lisp/editor-integration
      ln -snf $out/share/emacs/site-lisp $out/share/emacs/site-lisp/editor-integration/emacs
    '';
  });
  };
in
  generatedPkgs.overrideScope' overrides
