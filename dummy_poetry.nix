{ pkgs ? import <nixpkgs> {} }:
let
  poetry2nix = import (builtins.fetchTarball "https://github.com/nix-community/poetry2nix/archive/master.tar.gz") { inherit pkgs; };
in poetry2nix
