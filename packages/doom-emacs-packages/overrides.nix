{
  lib,
  newScope,
  fetchFromGitHub,
  fetchFromGitLab,
  fetchFromGitea,
  fetchgit,
  melpaBuild,
  elpaBuild,
  writeText,
  emacs,
  trivialBuild,
  lock,
  ocamlPackages,
  git,
}:

let
  # Load generated packages
  generatedPkgs = lib.makeExtensible (
    self:
    (import ./generated.nix {
      inherit
        lib
        newScope
        fetchFromGitHub
        fetchFromGitLab
        fetchFromGitea
        fetchgit
        melpaBuild
        elpaBuild
        writeText
        emacs
        trivialBuild
        ;
      # Pass emacs.pkgs as emacsPackages for external dependencies
      emacsPackages = emacs.pkgs;
    })
  );

  # Define overrides
  overrides = self: super: {
    evil-easymotion = super.evil-easymotion.overrideAttrs (
      {
        packageRequires ? [ ],
        ...
      }:
      {
        packageRequires = packageRequires ++ [ self.evil ];
      }
    );

    evil-markdown = super.evil-markdown.overrideAttrs (
      {
        packageRequires ? [ ],
        ...
      }:
      {
        packageRequires = packageRequires ++ [
          self.evil
          self.markdown-mode
        ];
      }
    );

    magit = super.magit.overrideAttrs (
      {
        version,
        packageRequires ? [ ],
        ...
      }:
      {
        preBuild = ''
          make VERSION="${version}" -C lisp magit-version.el
        '';

        packageRequires = packageRequires ++ [
          self.cond-let
          emacs.pkgs.llama
          emacs.pkgs.magit-section
          self.transient
          emacs.pkgs.with-editor
        ];
      }
    );

    orgit = super.orgit.overrideAttrs (
      {
        packageRequires ? [ ],
        ...
      }:
      {
        packageRequires = packageRequires ++ [
          self.cond-let
          self.magit
        ];
      }
    );

    transient = super.transient.overrideAttrs (
      {
        packageRequires ? [ ],
        ...
      }:
      {
        packageRequires = packageRequires ++ [ self.cond-let ];
      }
    );

    # elisp-def = super.elisp-def.overrideAttrs(_: {
    #   packageRequires = [ emacs.pkgs.dash ];
    # });
    # dumb-jump = super.dumb-jump.overrideAttrs (_: {
    #   packageRequires = [ emacs.pkgs.s emacs.pkgs.dash emacs.pkgs.popup ];
    # });
    #
    # evil-quick-diff = super.evil-quick-diff.overrideAttrs (_: {
    #   packageRequires = [ self.evil ];
    # });
    #
    # ox-clip = super.ox-clip.overrideAttrs (_: {
    #   packageRequires = [ emacs.pkgs.htmlize ];
    # });
    #
    # evil-vimish-fold = super.evil-vimish-fold.overrideAttrs (_: {
    #   packageRequires = [ self.evil ];
    # });
    #
    # evil-org = super.evil-org.overrideAttrs (_: {
    #   packageRequires = [ self.evil self.org ];
    # });

    straightBuild =
      { pname, ... }@args:
      self.trivialBuild (
        {
          ename = pname;
          version = "1";
          src = lock pname;
          buildPhase = ":";
        }
        // args
      );

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

    # use-package needs to be built with melpaBuild instead of elpaBuild
    # TODO: why needed?
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

    doom-snippets = self.straightBuild {
      pname = "doom-snippets";
      installPhase = ''
        mkdir -p $out/share/emacs/site-lisp
        cp -r $src/* $out/share/emacs/site-lisp/
      '';
    };

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

    tree-sitter = super.tree-sitter.overrideAttrs (esuper: {
      postInstall = ''
        ln -s ${super.tsc}/share/emacs/site-lisp/elpa/${super.tsc.name}/* \
          $out/share/emacs/site-lisp/elpa/${esuper.pname}-${esuper.version}/
      '';
    });

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
generatedPkgs.extend overrides
