<?php

declare(strict_types=1);

namespace DI\Test\UnitTest\Proxy\Fixtures;

class ClassToProxy
{
    protected bool $initialized = false;

    public function foo()
    {
        $this->initialized = true;
    }

    public function getInstance()
    {
        return $this;
    }
}
