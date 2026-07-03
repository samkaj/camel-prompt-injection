{
  description = "CaMeL Flake using FHS env for uv";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # An FHS (Filesystem Hierarchy Standard) environment is perfect for NixOS users
        # who want to run tools like `uv` that download pre-compiled binaries natively.
        fhs = pkgs.buildFHSEnv {
          name = "camel-fhs-env";
          targetPkgs = pkgs: with pkgs; [
            uv
            python311
            # Add any other system-level C-dependencies your python packages might need here
            zlib
            glib
            libGL
          ];
          runScript = "zsh"; # or bash, if preferred
          profile = ''
            # Make sure we use the standard Python location inside the FHS
            export UV_PYTHON_DOWNLOADS="never"
            export UV_PYTHON_PREFERENCE="system"

            echo "========================================="
            echo "Welcome to the CaMeL FHS Flake env!"
            echo "You are now in a standard Linux environment"
            echo "where uv and standard Python wheels work perfectly."
            echo "========================================="
          '';
        };
      in
      {
        devShells.default = fhs.env;
      });
}
