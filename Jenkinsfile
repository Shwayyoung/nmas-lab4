pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    environment {
        PYTHON = '/var/lib/jenkins/venvs/nmas-lab7/bin/python'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Python Syntax') {
            steps {
                sh '''
                    python3 -m py_compile automation/*.py web/*.py
                '''
            }
        }

        stage('Render Configs') {
            steps {
                sh '''
                    set -e
                    for device in R1 R2 R3 R4 R5 S1 S2 S3 S4; do
                        echo "=== Rendering $device ==="
                        python3 automation/render_config.py "$device"
                    done
                '''
            }
        }

        stage('Config Diff') {
            steps {
                sh '''
                    mkdir -p reports/config_changes
                    python3 automation/config_change.py --all
                '''
            }
        }

        stage('Unit Tests') {
            steps {
                sh '''
                    set -e
                    $PYTHON -m coverage erase
                    $PYTHON -m coverage run -m pytest \
                        tests/unit/ -v
                '''
            }
        }

        stage('Integration Tests') {
            steps {
                sh '''
                    set -e
                    $PYTHON -m coverage run -m pytest \
                        tests/integration/ -v
                '''
            }
        }

        stage('Coverage Reports') {
            steps {
                sh '''
                    set -e
                    mkdir -p reports

                    $PYTHON -m coverage combine
                    $PYTHON -m coverage report -m
                    $PYTHON -m coverage html
                    $PYTHON -m coverage xml
                    $PYTHON -m coverage json \
                        -o reports/coverage.json

                    $PYTHON tools/coverage_report.py
                '''
            }
        }
    }

    post {
        always {
            archiveArtifacts(
                artifacts: 'configs/generated/**/*.cfg,reports/config_changes/**/*.json,reports/coverage.xml,reports/coverage.json,reports/module_coverage.json,reports/coverage_html/**',
                allowEmptyArchive: true
            )
        }

        success {
            echo 'NMAS Lab7 CI pipeline passed.'
        }

        failure {
            echo 'NMAS Lab7 CI pipeline failed.'
        }
    }
}